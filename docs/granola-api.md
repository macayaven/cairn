# Granola public API shapes for a notes connector (list, get, transcript, folders, spaces)

Researched 2026-11-13. No Granola API was called. Every page below was fetched that day with
`curl` (the `.md` form of each docs page, which returns Markdown with the page's OpenAPI
fragment inlined) and read in full unless its line says otherwise; the connector and its tests are to be built from these notes,
not from memory. The API's own version on every reference page is `info.version: 1.7.0`.

- **INTRO** = https://docs.granola.ai/introduction.md (base URL, key scopes, quick start, errors, rate limits)
- **LLMS** = https://docs.granola.ai/llms.txt (the docs index; used only to find pages)
- **SPEC** = https://docs.granola.ai/api-reference/openapi.json (OpenAPI 3.1.0, `info.version` `1.7.0`; the per-page fragments below are cut from it)
- **LIST-NOTES** = https://docs.granola.ai/api-reference/list-notes.md
- **GET-NOTE** = https://docs.granola.ai/api-reference/get-note.md
- **GET-TRANSCRIPT** = https://docs.granola.ai/api-reference/get-transcript.md
- **LIST-FOLDERS** = https://docs.granola.ai/api-reference/list-folders.md
- **LIST-SPACES** = https://docs.granola.ai/api-reference/list-spaces.md
- **SEARCH-NOTES** = https://docs.granola.ai/api-reference/search-notes.md
- **SEARCH-TRANSCRIPTS** = https://docs.granola.ai/api-reference/search-transcripts.md
- **CHANGELOG** = https://docs.granola.ai/api-reference/changelog.md
- **WEBHOOKS** = https://docs.granola.ai/webhooks.md
- **CREATE-WEBHOOK** = https://docs.granola.ai/api-reference/create-webhook-endpoint.md (read for the request/response fields; schema descriptions skimmed)
- **LIST-WEBHOOKS** = https://docs.granola.ai/api-reference/list-webhook-endpoints.md (skimmed)
- **UPDATE-WEBHOOK** = https://docs.granola.ai/api-reference/update-webhook-endpoint.md (skimmed)
- **KEYS-HELP** = https://docs.granola.ai/help-center/sharing/integrations/granola-api.md (help-center page on API keys)
- **AUTO-DELETE** = https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion.md
- **SPACES-FOLDERS** = https://docs.granola.ai/help-center/sharing/folders/spaces-and-folders.md
- **SHARING** = https://docs.granola.ai/help-center/consent-security-privacy/sharing-controls.md
- **AUDIT** = https://docs.granola.ai/audit.md (Audit API guide; read for its rate-limit and key statements)
- **LEGAL-HOLDS** = https://docs.granola.ai/legal-holds.md
- **USER-TERMS** = https://docs.granola.ai/help-center/policies/terms-of-service/user-terms-of-service.md ("Last updated: 19th December 2025")
- **PLATFORM-TERMS** = the Platform Terms PDF. The docs page https://docs.granola.ai/help-center/policies/terms-of-service/platform-terms-of-service.md holds only a link to https://go.granola.ai/platform-tos-pdf, which redirected to https://www.granola.ai/static/Granola-Platform-Terms-2025-12-19.pdf (10 pages, "Last updated: 19th December 2025"); text extracted with `pdftotext -layout`, and the clauses below are paraphrased from that extraction. Sections 1 to 10.3 were read in full; sections 10.4 to 14 (refunds, survival, indemnity, warranties, miscellaneous) were only keyword-searched (terminat, retriev, delet, export, data, suspend).
- **APP-TERMS** = https://docs.granola.ai/help-center/policies/terms-of-service/application-terms-of-service.md (iOS app only; nothing on the API)

Not fetched (seen in LLMS, not needed or out of scope): delete-webhook-endpoint, audit-events,
list-audit-events, the legal-hold endpoint pages, the MCP page, the security and privacy FAQs,
the Privacy Policy and the Data Processing Addendum. No page failed to fetch (every request
answered 200).

Tags: **[doc: X]** = stated in prose or a schema description on page X; **[spec: X]** = a
schema fact (type, pattern, `required`, enum, min/max) on page X; **[example: X]** = only
visible in an example request or response on page X, not stated as a rule; **[not stated]**
= nowhere I could find in the pages above. **CONFLICT** = two pages (or a page and its
schema) disagree. **QUESTION** = a gap that matters for the connector's design.
**[cairn]** = what the Granola source (`src/cairn/sources/granola.py`) does about it.

---

## Headline points (read first)

1. **List notes is ordered newest first by creation time, not by update time.** It lists the
   notes the key can read, the most recently created first (paraphrased). [doc: LIST-NOTES] There is
   `updated_after` but no `updated_before`, and nothing says `updated_after` changes the
   order. [spec: LIST-NOTES] The tie-break between notes with the same `created_at` is
   **[not stated]**.
   **[cairn]** The source does not rely on the API's order: it collects every page,
   then reads the notes oldest `created_at` first, then by id ignoring case.
2. **A note is invisible until its AI summary exists, and then it appears with its old
   `created_at`.** The API returns a note only once its AI summary has been generated; a note
   still being processed, or never summarised, is left out of List notes and answers 404 on
   Get note (paraphrased). [doc: INTRO] Whether generating the
   first summary moves `updated_at` is **[not stated]**.
   **[cairn]** It never reads a note created before the source was switched on, by its
   own check of `created_at`. A note whose summary comes late is found by a later pull,
   or by the daily rescan when its `updated_at` did not move.
3. **What moves `updated_at` is not documented for List or Get.** Both describe it only as
   the time the note was last updated (paraphrased). [spec: LIST-NOTES, GET-NOTE] Search's
   `updated_after` speaks of a change to the content or the summary (paraphrased) [doc:
   SEARCH-NOTES]; the webhook `note.edited` fires when the note's summary is edited or
   generated again (paraphrased) [doc: WEBHOOKS]. Whether sharing a note
   with the key's owner, moving it into a folder or space, or switching on a space's API
   access moves `updated_at` is **[not stated]**, and those are exactly the ways an old note
   becomes newly readable by a key (webhooks have a separate `note.access_granted` event for
   it). [doc: WEBHOOKS]
   **[cairn]** Each pull lists by `updated_after`; once a day it also lists by
   `created_after` back to the later of the switch-on and `lookback_days` (30) ago, so a
   note that becomes readable without its `updated_at` moving is found within a day,
   if it was created inside that window. The two filters go in separate scans, never
   in one request.
4. **Cursor lifetime is stated only for the search endpoints.** A cursor lasts 15 minutes
   and works only with the key that received it (paraphrased). [doc: INTRO, about Search
   notes; SEARCH-NOTES and SEARCH-TRANSCRIPTS give the same 15 minutes]. For
   List notes, List folders, List spaces and Get transcript the cursor's lifetime and key
   binding are **[not stated]**.
   **[cairn]** A List notes or Get transcript cursor is followed at once, within the
   pull that received it, and never stored: the source's own cursor holds times and
   digests, not Granola's cursor.
5. **Rate limit: burst capacity 25 requests, time window 5 seconds, sustained 5
   requests/second (300/minute), per user or workspace** (the page gives these as separate rows). [doc: INTRO] The Audit page says every API key in the workspace
   draws on the same limit (paraphrased). [doc: AUDIT] No rate-limit header and no `Retry-After`
   is documented: **[not stated]**.
   **[cairn]** A 429 is waited out: `Retry-After` in seconds when present, at most 60;
   5 seconds, the stated window, when absent; at most 3 times, then the pull fails.
   A wait that would pass the run's deadline is not waited for.
6. **Private notes come back only to the note owner's own key; deleted notes only to keys
   that can reach all the workspace's notes.** [doc: GET-NOTE, LIST-NOTES] Which key kinds
   reach all the workspace's notes is **[not stated]**.
   **[cairn]** The source never writes private notes. A note with `deleted_at` set is
   not read; a note deleted after cairn read it stays in cairn (cairn does not take
   back).

---

## A. Requests in general: base URL, versioning, authentication, keys

- Base URL: every endpoint sits under `https://public-api.granola.ai/v1`, and the key goes as
  a Bearer token (paraphrased). [doc: INTRO] The spec's server is
  `https://public-api.granola.ai` (named `Production`) and every path starts `/v1/`.
  [spec: SPEC]
- Versioning: the version is in the path (`/v1`). The document version is `1.7.0`
  [spec: SPEC]; the changelog lists v1.0.0 (February 2026) to v1.7.0 (November 2026) as
  a list of changes, mostly additions; v1.7.0 also changed how phone and watch transcripts are split and timed (section D). [doc: CHANGELOG] A version header, or a deprecation policy, is
  **[not stated]**. The Audit page says a field's meaning, and whether it is there, change
  only with a new API version (paraphrased) [doc: AUDIT], said of audit events only.
- Authentication header: `Authorization: Bearer <key>`. Security scheme `ApiKeyAuth`:
  `type: http`, `scheme: bearer`, `bearerFormat: apiKey`. [spec: SPEC] Error `MISSING_API_KEY`
  (401): the `Authorization: Bearer` header is absent or badly formed (paraphrased). [doc:
  INTRO]
- Key format: every example uses `grn_YOUR_API_KEY`. [example: INTRO, WEBHOOKS] That keys
  start `grn_` as a rule, the prefix of a workspace key, and the key's length or alphabet
  are **[not stated]**.
- Key kinds and what each can read:
  - **Personal API key**, with one or both access scopes [doc: INTRO, KEYS-HELP]:
    - **Personal notes**: notes the key's owner owns, notes shared with the owner directly,
      and notes in private folders shared with the owner (paraphrased).
    - **Public notes**: notes everyone in the workspace can see, and notes in the Team
      space (paraphrased).
    - Notes are private unless made otherwise; the public notes scope reaches a note only
      when it sits in a folder everyone in the workspace can see, a Team-space folder for
      example (paraphrased). [doc: KEYS-HELP]
  - **Workspace API key** (paraphrased): admins manage it, it belongs to the workspace and
    not to one person, and unlike a personal key it neither expires nor depends on anyone's
    account. With **All spaces** it reads public notes and the notes of every space whose
    API access is on; admins can limit a key to chosen spaces instead, and it then reads
    only the notes and folders of those spaces. [doc: KEYS-HELP] **All spaces** also covers
    spaces whose API access is switched on later; a key limited to chosen spaces never
    reaches others; when a chosen space loses API access or is deleted, the key loses that
    space and gains nothing in its place (paraphrased). [doc: KEYS-HELP] A key's chosen
    spaces cannot be edited: a new key is made and the old one revoked (paraphrased).
    [doc: KEYS-HELP]
  - **Notes by ID** scope (paraphrased): some workspace keys have it; such a key can fetch a
    workspace note by its ID, while the list and search endpoints give it nothing. [doc:
    INTRO] How such a key is created is **[not stated]**.
  - **Audit API keys** and **Legal hold API keys** (Enterprise) read nothing else
    (paraphrased): an audit key can neither list nor fetch notes, folders, spaces or webhook
    endpoints, and those endpoints answer an audit key as if they did not exist [doc: AUDIT];
    a Legal hold key reads no notes, folders, audit log or webhook endpoints. [doc:
    LEGAL-HOLDS]
- Personal key expiry: implied by the statement that workspace keys, unlike personal ones, do
  not expire [doc: KEYS-HELP] and by `INVALID_API_KEY`, which covers a key that is unknown,
  revoked or expired [doc: INTRO];
  the lifetime of a personal key is **[not stated]**. QUESTION: how long does a personal key
  live, and is its expiry visible anywhere before it fails?
- Fields that depend on the kind of key: section C (`private_notes_*`, `deleted_at`,
  `space_membership`, `folder_membership[].space_id`).
- Who can make a key:
  - INTRO (paraphrased): every workspace member on a Business plan can make API keys.
    [doc: INTRO]
  - KEYS-HELP (paraphrased): every workspace member on a **Business** or **Enterprise** plan
    can make API keys [doc: KEYS-HELP]; its FAQ says a Business or Enterprise plan is needed to make API keys
    (paraphrased). [doc: KEYS-HELP]
  - Workspace keys (paraphrased): workspace admins on Business and Enterprise plans can make
    **workspace API keys** as well [doc: INTRO], and only workspace admins can make and
    manage them. [doc: KEYS-HELP]
  - **CONFLICT** (minor): INTRO's first sentence names only Business; KEYS-HELP names
    Business or Enterprise; CHANGELOG v1.0.0 gives API key authentication to workspace
    administrators, and v1.2.0 extends key making to users in Business and Enterprise
    workspaces (paraphrased). [doc: CHANGELOG] The free plan cannot make keys, by every page.
- Where keys are made: personal keys in the desktop app at **Settings → Connectors → API
  keys**; workspace keys at **Settings → Connectors → Workspace API keys**. [doc: INTRO,
  KEYS-HELP]
- Revocation: click **Revoke** on the key and confirm; the key is then disabled for good and
  listed under **Revoked**, and this cannot be reversed (paraphrased). [doc: INTRO] Personal and workspace keys are revoked each from its own page. [doc:
  KEYS-HELP] A revoked key's requests get 401 `INVALID_API_KEY`. [doc: INTRO]
- What an Enterprise admin can switch off (Settings → Workspace → General → Apps &
  connectors → API access) [doc: KEYS-HELP]:
  - **Note access**: **Personal notes**, **Public notes**, both or neither can be on
    (paraphrased).
  - **Read transcripts** (paraphrased): on unless switched off. Off, members' keys, admins'
    own included, no longer read word-for-word meeting transcripts: notes come back without
    a transcript and the transcript endpoint answers with an error, while titles, attendees,
    summaries and private notes can still be read.
  - Workspace API keys are outside these controls (paraphrased). So whether a workspace key can be
    stopped from reading transcripts is **[not stated]**.
  - Also: an IP allowlist exists (403 `IP_ALLOWLIST_DENIED`) [doc: INTRO]; where it is set
    is **[not stated]**. Admins can turn off a space's **Allow access with a workspace API
    key**; a new space starts with **Allow Granola API access** on (paraphrased). [doc:
    KEYS-HELP, SPACES-FOLDERS]
  - Attachments: on Enterprise plans, snapshots come back only while a workspace admin keeps
    Take snapshots of shared screens on (paraphrased). [doc: GET-NOTE]
- Sandbox: there is none at present; the page suggests testing with a few notes in a folder
  kept for the purpose (paraphrased). [doc: KEYS-HELP]
- Redirects, timeouts, compression, request ids in headers: **[not stated]**. Every error
  body carries a `requestId` (section E).

## B. List notes

`GET /v1/notes`, `operationId: list_notes`. It lists the notes the key can read, the most
recently created first, and only those whose summary has been generated (paraphrased).
[doc: LIST-NOTES]

### B.1 Query parameters

All optional (`required: false`). [spec: LIST-NOTES]

| Name | Schema | Description on the page |
|---|---|---|
| `created_before` | `anyOf: [string format date, string format date-time]`; examples `'2026-01-27'`, `'2026-01-27T15:30:00Z'` | notes created before the date (paraphrased) |
| `created_after` | same | notes created after the date (paraphrased) |
| `updated_after` | same | notes updated after the date (paraphrased) |
| `folder_id` | string, pattern `^[Ff][Oo][Ll]_[a-zA-Z0-9]{14}$` | notes in the folder or in any folder below it; case does not matter in the ID; the list folders endpoint gives the IDs (paraphrased) |
| `space_id` | string, pattern `^[Ss][Pp][Aa]_[a-zA-Z0-9]{14}$` | notes in the space; case does not matter in the ID; the list spaces endpoint gives the IDs (paraphrased) |
| `cursor` | string; example `eyJjcmVkZW50aWFsfQ==` | where to go on from (paraphrased) |
| `page_size` | integer, `minimum: 1`, `maximum: 30`, `default: 10` | the most notes a page holds (paraphrased) |

- There is no `updated_before`, no sort parameter and no `include_deleted`. [spec: LIST-NOTES]
- Timestamp format: a date (`YYYY-MM-DD`) or an RFC 3339 date-time. [spec: LIST-NOTES] The
  examples use `Z` and whole seconds. [example: LIST-NOTES] For List notes, what time zone a
  date-only value means, whether an offset other than `Z` is accepted, and whether fractional
  seconds are accepted are **[not stated]**. (Search's bodies say a date stands for midnight
  UTC [doc: SEARCH-NOTES]; that sentence is not on LIST-NOTES.)
  **[cairn]** Every bound it sends is an RFC 3339 date-time in whole seconds with `Z`.
- Bounds: the descriptions say before and after. Whether either bound is inclusive is
  **[not stated]** for List notes. (Search's `from_date`/`to_date` are inclusive, at or after
  and at or before [doc: SEARCH-NOTES]; Search's `updated_after` speaks of a change after the
  date; neither sentence is on LIST-NOTES.)
  **[cairn]** Every bound is one hour before the moment it stands for, to cover an
  exclusive bound, a clock some minutes off and a list that shows an edit a little late;
  the notes the overlap lists again are recognised by the digests in the cursor.
- Combining parameters (for example `updated_after` with `created_after`, or `folder_id` with
  `space_id`) is not described for List notes: **[not stated]**. For Search, `space_id` can
  be sent together with `folder_id` (paraphrased). [doc: SEARCH-NOTES]
  **[cairn]** A source takes at most one of `folder_id` and `space_id`, and sends it,
  with `page_size` 30, on every page of every scan.
- A `folder_id` or `space_id` the key cannot see: the LIST-NOTES responses list only 200,
  400, 401, 429 and 500 [spec: LIST-NOTES]; for Search it is a 404 saying the folder or space
  was not found [spec: SEARCH-NOTES]. What List notes answers is **[not stated]**.
- **Spec oddity**: the `components.schemas.ListNotesQuery` object in SPEC lacks `space_id`,
  although the operation's `parameters` list has it. [spec: SPEC]

### B.2 Ordering

- Most recently created first [doc: LIST-NOTES], i.e. `created_at` descending.
- QUESTION: the order among notes with the same `created_at` is **[not stated]**.
- QUESTION: whether `updated_after` changes the order (for example to `updated_at`) is
  **[not stated]**; nothing says it does.
- QUESTION: whether the cursor is a snapshot or a live position (what happens to a page
  sequence when a note is created, summarised, shared or deleted between pages) is
  **[not stated]**.
- **[cairn]** A scan runs to its last page within one pull; a note the live cursor misses
  is listed again by the next pull, through the one-hour overlap or the daily rescan.

### B.3 Paging

- Response fields `hasMore` (boolean, whether more notes remain) and `cursor` (string or
  null, where to go on from), both required. [spec: LIST-NOTES]
- As long as `hasMore` is `true`, sending `cursor` gets the next page (paraphrased). The
  quick-start repeats
  the filter (`created_after`) on the next page's request alongside `cursor`. [doc and
  example: INTRO] Whether the filters must, may or must not be repeated with a List notes
  cursor is **[not stated]**. (For Search the filters are not to be sent again with the
  cursor (paraphrased). [doc: INTRO])
  **[cairn]** It repeats the filters beside the cursor, as the quick-start does. An empty
  page with `hasMore` true is not the end; `hasMore` true with a null cursor fails the
  pull; more than 1,000 pages in one scan fails it with a message that says to lower
  `lookback_days` or name a folder or space.
- Whether a page can hold fewer than `page_size` notes, or none, while `hasMore` is true is
  stated for Search, Search transcripts, the Audit and Legal Holds APIs [doc: SEARCH-NOTES,
  AUDIT, LEGAL-HOLDS], and **[not stated]** for List notes.
- Cursor format: an opaque string; the example is base64 (`eyJjcmVkZW50aWFsfQ==`).
  [example: LIST-NOTES] Lifetime, key binding and the error for an expired one are
  **[not stated]** for List notes (Search: 15 minutes, key-bound, 400). QUESTION.

### B.4 Response 200

```
ListNotesOutput (all required):
  notes:   array of NoteSummary
  hasMore: boolean
  cursor:  string | null
NoteSummary (all required):
  id:         string, pattern ^not_[a-zA-Z0-9]{14}$   the note's ID
  object:     string, enum ["note"]
  title:      string | null
  owner:      User
  created_at: string, format date-time  when the note was created
  updated_at: string, format date-time  when the note was last updated
  deleted_at: string | null, format date-time
              when the note was deleted, null if it was not; only keys that reach
              all the workspace's notes see deleted notes (paraphrased)
User (both required):
  name:  string | null
  email: string, format email
```
[spec: LIST-NOTES]

[example: INTRO] (abridged on the page itself; the title replaced here):
```json
{
  "notes": [
    { "id": "not_1d3tmYTlCICgjy", "title": "<a meeting title>", ... },
    ...
  ],
  "hasMore": true,
  "cursor": "eyJjcmVkZW50aWFsfQ=="
}
```
Request:
`curl "https://public-api.granola.ai/v1/notes?created_after=2026-01-20" -H "Authorization: Bearer grn_YOUR_API_KEY"`,
then the same URL with `&cursor=...`.
[example: INTRO]

- `hasMore` is camelCase while every other field is snake_case. [spec: LIST-NOTES]
- A listed note carries no summary, attendees or calendar event: those need Get note.
  [spec: LIST-NOTES, GET-NOTE]

## C. Get note

`GET /v1/notes/{note_id}`, `operationId: get_note`. It returns one note with its summary,
attendees, calendar event and folders, and the transcript as well when `include=transcript`
is sent; a note the key cannot read, or one whose summary has not been generated, answers
`NOTE_NOT_FOUND` (paraphrased). [doc: GET-NOTE]

- Path `note_id`: required, pattern `^[Nn][Oo][Tt]_[a-zA-Z0-9]{14}$`, the note's ID, case
  not mattering. [spec: GET-NOTE]
- Query `include`: optional, string, enum `["transcript"]`. [spec: GET-NOTE]
- Responses: 200, 400, 401, 404, 413, 429, 500. [spec: GET-NOTE]

### C.1 Fields of `Note`

Required (per `required`): `id`, `object`, `title`, `owner`, `created_at`, `updated_at`,
`deleted_at`, `web_url`, `calendar_event`, `attachments`, `attendees`, `folder_membership`,
`summary_text`, `summary_markdown`, `private_notes_text`, `private_notes_markdown`,
`transcript`. `space_membership` is defined but **not** in `required`. [spec: GET-NOTE]

| Field | Type | Meaning on the page |
|---|---|---|
| `id` | string, `^not_[a-zA-Z0-9]{14}$` | the note's ID |
| `object` | `"note"` | object type |
| `title` | string \| null | the note's title |
| `owner` | User (`name` string \| null, `email` string) | the note's owner |
| `created_at` | date-time | when the note was created |
| `updated_at` | date-time | when the note was last updated |
| `deleted_at` | date-time \| null | as in List notes; only keys that reach all the workspace's notes see deleted notes (paraphrased) |
| `web_url` | string, uri | the note's address in the Granola web app; example `https://notes.granola.ai/d/f3e45e0f-24cc-480b-9a6c-8b1f5e3d7a2c` |
| `calendar_event` | CalendarEvent \| null | below |
| `attachments` | array of NoteAttachment | photos attached by hand and snapshots of shared screens, in the order stored; empty when there are none (paraphrased) |
| `attendees` | array of User | the meeting's attendees |
| `folder_membership` | array of Folder | the folders the note is in; since v1.1.0 the ancestor folders are listed beside the folders that hold it directly (paraphrased) [doc: CHANGELOG] |
| `space_membership` | array of Space | the spaces the note is in, through its folders, that the key can reach (paraphrased) |
| `summary_text` | string (not nullable) | the note's summary as text |
| `summary_markdown` | string \| null | the note's summary in Markdown; may be null when the note has no summary (paraphrased) |
| `private_notes_text` | string \| null | the owner's own private notes as plain text; returned only to a key of the user who created the note, `null` for every other key, shared notes and workspace-scoped keys included (paraphrased) |
| `private_notes_markdown` | string \| null | same, in Markdown |
| `transcript` | array of Transcript \| null | null unless `include=transcript` is sent, and null when a workspace admin has switched transcript access over the API off (paraphrased). Too large: 413 (C.3). |

[spec and doc: GET-NOTE]

- **CalendarEvent** (all six required): `event_title` (string \| null), `invitees` (array of
  `CalendarInvitee` = `{ email }`, email required), `organiser` (string \| null, format
  email: the organiser's email), `calendar_event_id` (string \| null; example
  `2su99n6iiik37iiknmb5t4fkfh_20260127T153000Z`), `scheduled_start_time` and
  `scheduled_end_time` (date-time \| null). [spec: GET-NOTE] The calendar provider and
  the meaning of `calendar_event_id` across providers are **[not stated]**. Search's
  `meeting_at` is the meeting's start by the calendar, or the note's creation time when there
  is no calendar event (paraphrased). [doc: SEARCH-NOTES]
- **NoteAttachment**: `id` (`^att_[a-zA-Z0-9]{14}$`), `object` `"attachment"`, `type`
  (`photo` or `screenshot`; a client is to skip types it does not know, paraphrased),
  `url` (it can change, and fetching the note again gives the current one; whoever holds it
  can open it, so it is to be handled as sensitive note content, paraphrased), `width`,
  `height` (number, pixels), the date-time a screenshot was taken (the spec names the field;
  left out for photos), `description` (a generated account of what the snapshot shows; left
  out for photos, paraphrased). Required: `id`, `object`, `type`,
  `url`, `width`, `height`. [spec: GET-NOTE]
- **Folder**: `id` (`^fol_[a-zA-Z0-9]{14}$`), `object` `"folder"`, `name` (string),
  `parent_folder_id` (`fol_...` \| null, null for a top-level folder), `space_id`
  (`spa_...` \| null, null when the folder is in no space or in one the key cannot reach). Required: `id`, `object`, `name`, `parent_folder_id` (not `space_id`).
  [spec: GET-NOTE]
- **Space**: `id` (`^spa_[a-zA-Z0-9]{14}$`), `object` `"space"`, `name`; all required.
  [spec: GET-NOTE]
- **Id prefixes**: `not_` (note), `fol_` (folder), `spa_` (space), `att_` (attachment),
  `whe_` (webhook endpoint, [example: WEBHOOKS]), `usr_` (user, LEGAL-HOLDS), `aud_` (audit
  event, AUDIT); each note/folder/space/attachment id is the prefix plus 14 ASCII letters or
  digits in the response patterns. [spec: GET-NOTE] Path and query patterns accept any case
  of the prefix (the descriptions say case does not matter). [spec: GET-NOTE, LIST-NOTES]

[example: INTRO] (abridged on the page, and further here; the texts replaced here):
```json
{
  "id": "not_1d3tmYTlCICgjy",
  "title": "<a meeting title>",
  "owner": { "name": "Oat Benson", "email": "oat@granola.ai" },
  "summary_text": "<the summary, as plain text> ...",
  "transcript": [
    { "speaker": { "source": "microphone" }, "text": "...",
      "start_time": "2026-01-27T15:30:00Z", "end_time": "2026-01-27T15:30:04Z" },
    ...
  ],
  ...
}
```
for `GET /v1/notes/not_1d3tmYTlCICgjy?include=transcript`. [example: INTRO]

### C.2 What depends on the kind of key

- `private_notes_text` / `private_notes_markdown`: only for a key of the user who created the
  note; `null` for shared notes and workspace-scoped keys. [doc: GET-NOTE] They stay
  available when an Enterprise admin turns transcripts off. [doc: KEYS-HELP]
- `deleted_at` non-null: only for keys that reach all the workspace's notes. [doc: GET-NOTE]
  Which keys those are is **[not stated]** (see F).
- `space_membership` and `Folder.space_id` are limited to spaces the key can access.
  [doc: GET-NOTE]
- `transcript`: null when an admin has turned off transcript access over the API (does not
  apply to workspace keys, A). [doc: GET-NOTE, KEYS-HELP]
- Attachments of type `screenshot` depend on an Enterprise admin setting. [doc: GET-NOTE]

### C.3 404 and 413

- 404 `NOTE_NOT_FOUND` for a note the key cannot read or whose summary has not been
  generated. [doc: GET-NOTE] Error table (paraphrased): 404 `NOT_FOUND`, `NOTE_NOT_FOUND`
  when the resource is missing or the key cannot read it, and also when the key's access
  scope may not use the endpoint. [doc: INTRO] So a 404 does not say whether a note was deleted, unshared, never
  summarised or never existed. **[not stated]** beyond that.
- **Spec oddity**: `summary_markdown` may be null when the note has no summary, yet a note
  without a generated summary answers 404; when a 200 can carry a null `summary_markdown` is
  **[not stated]**. `summary_text` is a non-nullable string. [spec: GET-NOTE]
- 413 `TRANSCRIPT_TOO_LARGE` (paraphrased): when a transcript is too big to come back inside
  the note, Get note answers `413` with `TRANSCRIPT_TOO_LARGE`, and the whole transcript is to be read from
  `/v1/notes/{note_id}/transcript`. [doc: INTRO, GET-NOTE] Only with `include=transcript`,
  by implication; that a 413 never happens without `include` is not stated outright. The
  size threshold is **[not stated]**. The 413 carries no note: the whole Get note fails.
  QUESTION: is the threshold in bytes or items, and is it stable?
  **[cairn]** On a 413 it asks Get note again without `include` and reads the transcript
  from Get transcript. A 404 is skipped as gone and counted as read. A 200 with a null
  `summary_markdown` falls back to `summary_text`, then to a fixed sentence.

## D. Transcript

`GET /v1/notes/{note_id}/transcript`, `operationId: get_transcript`. It reads a meeting's
transcript page by page, also when Get note answers `TRANSCRIPT_TOO_LARGE` (paraphrased).
[doc: GET-TRANSCRIPT] Added in v1.4.0. [doc: CHANGELOG]

- Parameters [spec: GET-TRANSCRIPT] (descriptions paraphrased):
  - `note_id` (path, required, `^[Nn][Oo][Tt]_[a-zA-Z0-9]{14}$`).
  - `cursor` (query, string): the opaque cursor the previous page gave.
  - `from_time` (query, date-time): begins at the first transcript item ending after this UTC
    time; ignored when `cursor` is sent, so it can go on every page; a recording transcribed
    as one piece without timings from Granola comes back from its start.
  - `page_size` (query, integer, `minimum: 1`, `maximum: 100`, `default: 50`): the most
    transcript items a page holds.
- Responses: 200, 400 (a bad request or a cursor that is not valid), 401, 403 (forbidden:
  a workspace admin has switched transcript access over the API off), 404, 429, 500.
  [spec: GET-TRANSCRIPT]
- Response 200 `GetTranscriptOutput` (all required): `transcript` (array of Transcript, this
  page's items), `hasMore` (boolean), `cursor` (string \| null, null on the last page). [spec: GET-TRANSCRIPT] Cursor lifetime: **[not stated]**.
- **Transcript item** (required: `speaker`, `text`, `start_time`, `end_time`) [spec:
  GET-TRANSCRIPT, GET-NOTE]:
  - `text`: string.
  - `start_time`, `end_time`: string, format date-time, i.e. **absolute wall-clock
    timestamps, not offsets from the start of the meeting**. Examples are UTC with `Z`, whole
    seconds. [example: INTRO] For phone and watch recordings (paraphrased) the times
    are approximate and counted from the note's creation; when Granola has no timings for
    such a recording, all items carry one time, the moment the transcript was written, and
    `end_time` is then the same as `start_time`. [doc: GET-TRANSCRIPT] Before v1.7.0 every
    item of such a recording had the time the transcript was written. [doc: CHANGELOG]
    **[cairn]** Transcript times are written as offsets from the calendar event's
    scheduled start, else from the first item, never from `created_at`.
  - `speaker` (required: `source`):
    - `source`: enum `microphone`, `speaker`. On macOS an item comes either from the
      machine's own microphone or from the rest of the meeting's audio (paraphrased). [doc:
      INTRO] Phone/watch (paraphrased): every item is `microphone` for now, and a client is
      not to count on that staying so. [doc: GET-TRANSCRIPT]
    - `attribution`: enum `me` (the note's owner), `them` (other participants); left out when
      the attribution is not known. Added v1.3.0. [doc: GET-TRANSCRIPT, CHANGELOG]
    - `diarization_label`: string such as `Speaker A`, for now only on transcripts recorded
      on a phone or watch where diarization is available (paraphrased).
    - `name`: string, the name of the speaker when one was identified, and only then
      (paraphrased).
- [example: GET-NOTE] (first of two items, reflowed here, its text replaced; note the one-hour item):
  ```yaml
  - speaker: { source: microphone, attribution: me }
    text: <one sentence of speech>
    start_time: '2026-01-27T15:30:00Z'
    end_time: '2026-01-27T16:30:00Z'
  ```
- Item granularity changed in v1.7.0 (paraphrased): Android and Apple Watch transcripts now
  come as one item per speaker line, where before the whole transcript was one item given to
  the note's owner. v1.7.0 also fixed some notes made on iOS that returned only their
  transcript's last line. [doc: CHANGELOG] So a transcript fetched
  before and after a server change can differ for the same note.
- Size limits: page size max 100 items [spec]; any byte limit on the paged endpoint is
  **[not stated]**; the inline limit is the 413 (C.3).
  **[cairn]** It reads at most 500 pages of 100 items; a longer transcript is skipped
  with a reason, and the summary is still written.
- When a transcript is absent:
  - Admin turned off transcript access (members' keys on Enterprise): inline `transcript` is
    null; the transcript endpoint answers 403 (`FORBIDDEN`, listed for reading transcripts
    while a workspace admin has switched transcript access over the API off). [doc: GET-NOTE,
    GET-TRANSCRIPT, INTRO]
    **[cairn]** A 403 whose code is `FORBIDDEN` skips the transcript with a reason and
    writes the summary; any other 403 fails the pull, since a note is read once.
  - Auto-deletion: users on non-Enterprise plans can set a period (1 day to 1 year, or Off);
    Enterprise admins can set one for all members, which overrides personal settings; each
    change starts a one-week cooldown; deletion is for good and cannot be reversed; notes
    themselves are left alone; after deletion the AI notes can no longer be regenerated. [doc:
    AUTO-DELETE] What the API answers for a note whose transcript was auto-deleted (null,
    `[]`, 404 or 403 on the transcript endpoint) is **[not stated]**. QUESTION.
  - Plan: whether any plan (or key kind) gets notes without transcripts is **[not stated]**.
  - A note with no audio or an empty transcript: **[not stated]** whether `[]` or null.
    **[cairn]** An inline `[]`, a Get transcript 404 or an empty transcript is skipped
    as "no transcript"; an inline null is read from Get transcript.
- Legal holds (paraphrased): while a hold is active, Granola does not permanently delete the
  notes, transcripts and attachments of the people it covers. [doc: LEGAL-HOLDS] Whether a held, auto-deleted transcript is still returned by the API is
  **[not stated]**.

## E. Errors and limits

### E.1 Error body

All error responses share one JSON body (paraphrased) [doc: INTRO]:
```json
{
  "code": "VALIDATION_ERROR",
  "message": "Invalid query parameters",
  "details": [{ "field": "page_size", "issue": "Number must be less than or equal to 30" }],
  "requestId": "0c6f3f0e-7f0f-4c59-9d0b-2f1f6f1a3b7e",
  "timestamp": "2026-01-27T15:30:00.000Z"
}
```
[example: INTRO] `details` comes only with some errors, validation errors among them, and
support asks for the `requestId` (paraphrased). [doc: INTRO] SPEC declares no error schema: a script
over SPEC found no non-2xx response with `content` on any operation, so this shape rests on
INTRO alone. [spec: SPEC]
Whether `message` can echo request input (for example a cursor or key fragment) is
**[not stated]**.

### E.2 Status codes and codes [doc: INTRO]

| Status | Code | Meaning (in our words) |
|---|---|---|
| 400 | `VALIDATION_ERROR` | a parameter or the body is not valid; `details` names each problem (paraphrased) |
| 401 | `MISSING_API_KEY` | the `Authorization: Bearer` header is absent or badly formed (paraphrased) |
| 401 | `INVALID_API_KEY` | the key is unknown, revoked or expired (paraphrased) |
| 403 | `IP_ALLOWLIST_DENIED` | the request came from a network not on the workspace's IP allowlist (paraphrased) |
| 403 | `ACCOUNT_SUSPENDED` | the account that made the key is suspended (paraphrased) |
| 403 | `SUBSCRIPTION_INACTIVE` | the workspace's subscription is not active (paraphrased) |
| 403 | `FORBIDDEN` | the key may not do this, e.g. transcripts turned off (paraphrased) |
| 404 | `NOT_FOUND`, `NOTE_NOT_FOUND` | the resource is missing or the key cannot read it, and also when the key's access scope may not use the endpoint (paraphrased) |
| 413 | `TRANSCRIPT_TOO_LARGE` | Get note only; use Get transcript |
| 429 | `RATE_LIMITED` | too many requests (paraphrased) |
| 500 | `INTERNAL_ERROR` | a fault on Granola's side (paraphrased) |

- Legal holds add `CUSTODIAN_NOT_IN_WORKSPACE` (400) and `LEGAL_HOLD_NAME_CONFLICT` (409).
  [doc: LEGAL-HOLDS]
- The per-endpoint specs list fewer statuses than the table (LIST-NOTES: 200, 400, 401, 429,
  500; no 403 or 404) [spec: LIST-NOTES]; the INTRO table is the global list. 502, 503 and
  504 are not listed anywhere: **[not stated]**.
- An expired or invalid cursor: 400 on Get transcript (a bad request or a cursor that is not
  valid) and Search (an expired cursor) [spec]; on List notes **[not stated]**.
- **CONFLICT** (paraphrased): for a key with the Notes by ID scope, the list and search
  endpoints give nothing [doc: INTRO, quick-start section], while the error table says a 404
  also comes when the key's access scope may not use the endpoint [doc: INTRO], and Search's
  spec lists a 404 for an access scope that may not use it [spec: SEARCH-NOTES]. Whether List
  notes gives such a key an empty page or a 404 is unclear.
- Audit keys on note endpoints: those endpoints answer as if they did not exist. [doc: AUDIT]
- `SUBSCRIPTION_INACTIVE` and `ACCOUNT_SUSPENDED` mean a key can start failing with 403 for
  billing or account reasons, not for the request. [doc: INTRO]

### E.3 Rate limits

- INTRO (paraphrased): limits are counted per user or per workspace, by the key's access
  scope, to keep use fair and the platform stable. Burst capacity 25 requests; time window 5
  seconds; sustained rate 5 requests/second (300/minute). Going over the limit answers
  `429 Too Many Requests`. [doc: INTRO]
- KEYS-HELP (paraphrased): a key gets a burst of 25 requests and 5 requests/second
  sustained, and limits may be counted per user or per workspace by the key's access scope.
  [doc: KEYS-HELP]
- AUDIT (paraphrased): every API key in the workspace draws on one limit, so a backfill is to
  be spread out in time, not run in parallel. [doc: AUDIT] Said on the Audit page; whether it
  holds for note-reading keys too is not clear (INTRO says per user or per workspace).
  QUESTION.
- Which scope maps to per user and which to per workspace is **[not stated]**.
- Rate-limit response headers (`X-RateLimit-*`, `RateLimit-*`) and `Retry-After`:
  **[not stated]** (searched INTRO, KEYS-HELP, AUDIT, every reference page fetched, and SPEC;
  a script over SPEC found no response with a `headers` key on any operation). QUESTION: does a 429 carry `Retry-After`?
- Request size limits: **[not stated]**. Body limits exist only on the POST search bodies
  (`keywords` up to 20 entries of up to 200 or 100 characters, `cursor` up to 2048
  characters, `note_ids` up to 100). [spec: SEARCH-NOTES, SEARCH-TRANSCRIPTS] Response size
  limits other than the 413: **[not stated]**.
- Search gives up once it has looked at 10,000 notes without finishing (`completion_status:
  incomplete`). [doc: SEARCH-NOTES]

## F. Change semantics

- `updated_at`: when the note was last updated (List, Get; paraphrased). [spec] No list of
  what moves it. **[not stated]** for: first summary generation, summary edit, summary regeneration,
  title edit, private-notes edit, attendee or calendar change, transcript arrival or
  auto-deletion, attachment added, sharing, folder or space membership change, deletion.
  - Related statements (paraphrased): Search `updated_after` keeps notes whose content or
    summary changed after the date [doc: SEARCH-NOTES]; webhook `note.edited` fires when the
    summary is edited or generated again, and its `changed_fields` is for now always
    `["summary"]` [doc: WEBHOOKS].
- Can a note change long after its meeting? Summaries can be edited or generated again (the
  webhook events) [doc: WEBHOOKS]; regeneration stops working once the transcript is
  auto-deleted [doc: AUTO-DELETE]. Any time limit on edits is **[not stated]**. A note can also become
  readable to a key long after it was created: sharing, directly or through a folder
  [doc: WEBHOOKS], being added to a Team-space folder (KEYS-HELP), a space gaining API
  access (KEYS-HELP: **All spaces** also covers spaces whose API access is switched on later),
  or its first summary being generated late (INTRO). Whether any of these moves
  `updated_at` is **[not stated]**. QUESTION (the most important one).
- A note can stop being readable: unshared, moved out of a folder or space, the space's API
  access turned off, the key's scope edited (a key's access scopes are changed later with
  **Edit** [doc: INTRO]). Get note's 404 covers a note the key cannot read (C.3); that List
  notes signals the removal in any way is **[not stated]**.
- Deletion:
  - `deleted_at` (paraphrased): when the note was deleted, null if it was not; only keys that
    reach all the workspace's notes see deleted notes. [doc: LIST-NOTES, GET-NOTE]
  - What reaching all the workspace's notes means in terms of key kinds (a workspace key with
    All spaces? none of the personal scopes?) is **[not stated]**.
  - Whether List notes returns deleted notes to such keys (and in which order, and whether
    `updated_after` finds them) is **[not stated]**; the field's presence on `NoteSummary`
    suggests it but does not say it.
  - What every other key gets for a deleted note (404, absence from List notes) is
    **[not stated]**; the 404 rule covers a resource the key cannot read [doc: INTRO] without naming
    deletion. Whether deletion is soft (restorable) is **[not stated]**.
  - There is no webhook event for deletion (only `note.generated`, `note.edited`,
    `note.access_granted`). [doc: WEBHOOKS]
- Id stability: that a note's `id` never changes is **[not stated]**. The `aud_` audit id is
  opaque and does not change [doc: AUDIT], said of audit events only. Attachment `url`s can
  change. [doc: GET-NOTE] Folder and space ids are case-insensitive on input. QUESTION:
  is `not_...` stable across a note's moves between folders, spaces and workspaces (AUDIT
  lists a `notes.transfer_out` action [doc: AUDIT])?
- Transcript content can change for the same note across server releases (D, v1.7.0).
  [doc: CHANGELOG]

## G. Webhooks (brief; not the connector's primary mechanism)

- Plans: Business and Enterprise. [doc: WEBHOOKS] Create in the app (Settings → Connectors →
  Webhooks, or a folder's Integrations) or with `POST /v1/webhook-endpoints`. [doc: WEBHOOKS]
- Create body (paraphrased): `url` (HTTPS, reachable from the internet; addresses on private
  networks are refused), `scopes` (`personal`, `public`, or exactly `["workspace"]` with a
  workspace key, which cannot be changed afterwards), `events` (omit for all), `folder_ids`
  (events only for notes in those folders or subfolders). Response 201 includes
  `signing_secret` (`whsec_...`), given in that response only and never again. [doc:
  WEBHOOKS, CREATE-WEBHOOK] Endpoint fields: `id` (`whe_...`), `object`
  `"webhook_endpoint"`, `url`, `url_redacted` (true when the caller did not create the
  endpoint, since the path can hold credentials), `events`, `folder_ids`, `scopes`,
  `created_by` (User or null), `enabled`, `created_at`. [spec: LIST-WEBHOOKS] 404 when the
  workspace has no webhooks API. [spec: CREATE-WEBHOOK]
- Events (paraphrased): `note.generated` (a note's first AI summary is generated),
  `note.edited` (its summary is edited or generated again), `note.access_granted` (a note is
  shared with the endpoint's owner, directly or through a folder). `note.generated` is sent
  only when the first summary is generated while the endpoint can reach the note; a note
  shared later, after its summary exists, gives `note.access_granted` instead. [doc:
  WEBHOOKS]
- Payload [example: WEBHOOKS]:
  ```json
  { "event_id": "8f1c2a4e-6b3d-4e8f-9a2b-1c5d7e9f0a3b", "event_type": "note.generated",
    "note_id": "not_1d3tmYTlCICgjy", "occurred_at": "2026-01-27T15:30:00Z" }
  ```
  `note.edited` adds `data.changed_fields`. A payload holds none of the note's content, and
  access is checked when the note is fetched (paraphrased). [doc: WEBHOOKS]
- Signing: Standard Webhooks. Headers `webhook-id` (= `event_id`), `webhook-timestamp` (Unix
  seconds of this attempt), `webhook-signature` (`v1,` + base64 HMAC-SHA256; space-separated
  list per the sample code). Signed content `{webhook-id}.{webhook-timestamp}.{body}`, keyed
  with the part of the secret after `whsec_`, base64-decoded. A request whose
  `webhook-timestamp` is older than a few minutes is to be refused (no number given;
  paraphrased). [doc: WEBHOOKS]
- Delivery (paraphrased): 15 seconds to respond. 2xx delivered; 408, 429, 5xx, network errors
  and timeouts retried; 3xx failed for good, redirects not followed; other 4xx failed for
  good. Failed deliveries are retried with exponential backoff for four days, each retry
  with the same `event_id`. After four days without success Granola switches the endpoint
  off and emails its creator or the workspace admins; events missed while it is off are not
  sent again. [doc: WEBHOOKS] Delivery order is **[not stated]**.

## H. Folders and spaces

- `GET /v1/folders` (`list_folders`): lists the folders the key can read, by name
  (paraphrased). Query `cursor`, `page_size` (1 to 30, default 10). Response `{ folders: Folder[],
  hasMore, cursor }`, all required. Folder fields as in C.1 (`id`, `object`, `name`,
  `parent_folder_id`, `space_id`). Responses 200, 400, 401, 429, 500. [spec: LIST-FOLDERS]
- `GET /v1/spaces` (`list_spaces`): lists the spaces the key can reach, by name
  (paraphrased). Query `cursor`, `page_size` (1 to 30, default 10). Response `{ spaces:
  Space[], hasMore, cursor }`. Space fields `id`, `object`, `name`. 404: the workspace does
  not have spaces switched on. [spec: LIST-SPACES] Added v1.6.0. [doc: CHANGELOG]
- To limit the connector to one folder or space: List notes takes `folder_id` (the folder and
  every folder below it) or `space_id`. [spec: LIST-NOTES] Get note returns
  `folder_membership` (with ancestors) and `space_membership`, so a fetched note's membership
  can be checked. [spec: GET-NOTE, doc: CHANGELOG]
- Folder facts from the help center [doc: SPACES-FOLDERS]: two built-in spaces, **My notes**
  (private) and the team space; admins can create custom spaces; subfolders go one level
  deep only; a note can be in any number of folders; subfolders inherit their space;
  deleting a space cannot be undone, but its notes and folders are not deleted, only taken
  out of the space (paraphrased). Recurring meetings can be auto-added to a
  folder.
- What List notes with `folder_id` answers when the folder is later deleted or moved out of
  the key's reach is **[not stated]**. Whether a folder's id survives a rename or a move to
  another space is **[not stated]**.

## I. Terms that bear on an automated copy to another server

Paraphrased in our words, clause by clause; not interpreted.

**What the docs say about API use** (paraphrased)
- The API lets a workspace's own tools read its meeting notes, summaries and transcripts.
  [doc: INTRO]
- Use cases listed under "What can you build?": **Knowledge bases & wikis**, sending meeting
  notes to Notion, Confluence or a wiki so the team can search them and they stay current;
  and **Custom AI agents**, agents that join meeting context with other data sources.
  [doc: KEYS-HELP]
- Webhooks: the receiver reads the latest data through the Granola API. [doc: WEBHOOKS]
- Audit: whoever must keep audit events longer than a year is to pull them into a system of
  their own on a regular schedule (about audit events). [doc: AUDIT]
- The Audit API records API use: `workspace.public_api_key_used`,
  `workspace.public_api_key_first_used`; and its section on data that left Granola lists
  `data_export.requested`, `data_export.succeeded`, `notes.transfer_out`,
  `integration.*_connected`. [doc: AUDIT]
- Attachment URLs: whoever holds one can open it, so it is to be handled as sensitive note
  content. [doc: GET-NOTE]

**User Terms of Service** (for an Authorized User; "Last updated: 19th December 2025")
[doc: USER-TERMS], paraphrased:
- Definition: Services are Granola's commercially offered online software-as-a-service
  platform and services, any related APIs Granola provides included, with all the related
  mobile and desktop applications.
- Customer Data: the user accepts that the Customer owns the Customer Data; the Customer may
  switch third-party integrations on or off and manage permissions, retention and export
  settings.
- In the paragraph where the user promises not to contribute anything to, or otherwise use
  or interact with, the Services in certain ways, the lettered clauses include:
  - (f) running Listserv, an auto-responder or spam on the Services, or any process that runs
    or is set off while the user is not logged in, or that otherwise disturbs the Services'
    proper working (an unreasonable load on their infrastructure included);
  - (g) crawling, scraping or spidering any page, data or part of the Services, or content
    offered through them, by hand or by automated means;
  - (h) copying or storing any significant part of the content offered through the Services;
  - followed by: breaking any of these is grounds for ending the user's right to use or
    reach the Services.

**Platform Terms** (the Customer's agreement; PDF dated 2025-12-19) [doc: PLATFORM-TERMS],
paraphrased:
- 1.3: Customer Data is information that comes from the Customer and that the Customer or
  its Authorized User enters or uploads to the Services; System Data is not Customer Data.
- 1.7: Granola IP covers System Data and anything derived from Granola providing the
  Services; Granola IP does not cover Customer Data.
- 1.9: Non-Granola Applications cover application programming interfaces (APIs) and other
  technology made, owned or licensed by others than Granola that may work or integrate with
  the Services.
- 3.1: Granola grants a right to reach and use the Services during the Subscription Period,
  only for the Customer's internal business purposes and as the Documentation says.
- 3.2: the Customer keeps its own systems, accounts and access credentials secure, and (c)
  uses the Services only as this Agreement, the Documentation, Order Forms and applicable
  laws and government regulations allow.
- 3.4 Use Restrictions (the Customer may not, nor let any third party, directly or
  indirectly, do any of the following); the roman numerals repeat in the PDF as printed (two
  (v), (vi), (vii), (x) and (xi)); given here with the printed numerals:
  - (iii) sell, resell, copy, rent, license, lease, distribute, pledge, assign, sublicense or
    publish the Services, or otherwise transfer or encumber rights to them, or offer any
    Service as part of a service bureau or outsourcing offering;
  - (iv) use the Services for a third party's benefit, or make them available to any third
    party;
  - (vi) use the Services to build an application, service or product that competes with a
    Granola product or service or has ideas, features, functions or graphics like the
    Service's; or copy any of the Service's ideas, features, functions, data or graphics;
  - (vii) interfere with or disrupt the integrity or performance of a Service or of the data
    in it (the second (vii));
  - (ix) allow access to or use of a Service that gets around a contractual usage limit, or
    use a Service to reach, copy or use Granola intellectual property beyond what this
    Agreement permits;
  - (xvi) frame or mirror any part of a Service.
- 3.5 Non-Granola Applications: any data exchange or other dealing between the Customer and
  the third-party provider of a Non-Granola Application is a matter between those two alone,
  under that provider's own terms and conditions.
- 3.6: when the Customer is told that data has to be removed, changed or disabled so as not
  to breach the Agreement, applicable law or third-party rights, the Customer does so
  promptly, which can mean deleting data it downloaded from the Services.
- 4.1 (Free Services): the Customer alone is responsible for retrieving its Customer Data
  from Free Services, as far as they support it, before termination.
- 6.2.1: Granola does not sell Customer Data.

**Application Terms** concern the iOS app only (licensed for private, personal,
non-commercial use) and say nothing about the API. [doc: APP-TERMS]

---

## Open questions for the connector

Most important first.

1. QUESTION: What moves `updated_at`? In particular, does a note that becomes readable to the
   key late (first summary generated after creation, shared with the owner, added to a
   Team-space folder, its space gaining API access, the key's scope edited) get an
   `updated_at` at that moment, or keep an old one? If not, `updated_after` alone cannot find
   it. (B, F)
2. QUESTION: With `updated_after`, are results still ordered by `created_at` descending, and
   what is the tie-break between notes with the same `created_at`? (B.2)
3. QUESTION: Are `created_after`, `created_before` and `updated_after` inclusive or exclusive,
   what time zone does a date-only value mean, and what precision do the timestamps have
   (examples show whole seconds; is `updated_at` truncated)? (B.1)
4. QUESTION: How long does a List notes cursor live, is it bound to the key, is it a snapshot
   or a live position, must the filters be repeated with it, and what does an expired one
   answer? (B.3)
5. QUESTION: Do deleted notes appear in List notes for keys that can see them, which keys are
   those (keys that reach all the workspace's notes), and what does every other key see for a deleted
   note (404 only)? Is deletion restorable? (F)
6. QUESTION: Does a 429 carry `Retry-After` or any rate-limit header, which keys share a
   bucket (per user or per workspace, versus one limit for every key in the workspace),
   and are 502/503/504 possible? (E.3)
7. QUESTION: What does the API return for a note whose transcript was auto-deleted (inline
   null or `[]`; transcript endpoint 404, 403 or an empty page), and for a note that never had
   a transcript? (D)
8. QUESTION: What is the 413 `TRANSCRIPT_TOO_LARGE` threshold, and can a note that was
   fetched inline once later answer 413? (C.3)
9. QUESTION: Are note ids (`not_...`) stable for a note's life, including across folder,
   space and workspace moves (`notes.transfer_out`)? (F)
10. QUESTION: When can a 200 carry `summary_markdown: null`, given that a note without a
    generated summary is a 404? (C.3)
11. QUESTION: For a Notes-by-ID key, does List notes answer an empty page or 404 (CONFLICT in
    E.2)? And what does List notes answer for a `folder_id` or `space_id` the key cannot see?
    (B.1, E.2)
12. QUESTION: How long does a personal API key live before it expires, and can a workspace
    key be stopped from reading transcripts? (A)
