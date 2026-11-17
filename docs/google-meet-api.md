# Google Meet REST API v2: reading finished conferences, transcripts, entries and participants

Researched 2026-11-13. No Meet API was called. Every page below was fetched that day with a
plain HTTPS GET (`?hl=en`), converted to text and read in full unless a note says otherwise;
code samples in Java, Node.js, Python and Apps Script were skipped, cURL samples read. Final
URLs are given after redirects, with each page's "Last updated" line. The token side
(service account, refresh token, error envelope) is in google-auth.md.

Reference (base https://developers.google.com/workspace/meet/api/reference/rest/v2):

- **REST** = .../reference/rest/v2 (asked for as .../reference/rest; last updated 2026-09-11)
- **CR** = .../v2/conferenceRecords (2025-04-02); **CR-LIST** = .../conferenceRecords/list
  (2026-09-14); **CR-GET** = .../conferenceRecords/get (2026-09-14)
- **TR** = .../v2/conferenceRecords.transcripts (2026-09-14); **TR-LIST** = .../list
  (2026-09-14); **TR-GET** = .../get (2026-09-14)
- **DOCSDEST** = .../v2/DocsDestination (2026-04-02)
- **TE** = .../v2/conferenceRecords.transcripts.entries (2025-04-02); **TE-LIST** = .../list
  (2026-09-14); **TE-GET** = .../get (2026-09-14)
- **PA** = .../v2/conferenceRecords.participants (2026-09-14); **PA-LIST** = .../list
  (2026-09-14); **PA-GET** = .../get (2026-09-14)
- **SP** = .../v2/spaces (2026-04-16); **SP-GET** = .../spaces/get (2026-09-14)
- **DISCOVERY** = https://meet.googleapis.com/$discovery/rest?version=v2 (listed on REST;
  `revision` 20261109). Used to check scopes, filter fields and field formats.

Guides (base https://developers.google.com/workspace/meet/api/guides):

- **G-OVERVIEW** = .../overview (2026-09-14)
- **G-AUTH** = .../authenticate-authorize (2026-09-14)
- **G-CONF** = .../conferences (2026-09-14)
- **G-ARTIFACTS** = .../artifacts (2026-09-14). Read all but the "Recordings" and "Smart notes"
  sections, which were not read.
- **G-PARTICIPANTS** = .../participants (2026-09-14)
- **G-SPACES** = .../manage-meeting-spaces (asked for as .../meeting-spaces; 2026-09-14)
- **G-SPACES-OV** = .../meeting-spaces-overview (2026-09-14). Its permissions table is drawn
  with icons; read from the HTML's `compare-yes` / `compare-no` classes.
- **G-SPACES-CONFIG** = .../meeting-spaces-configuration (2026-09-14)
- **G-EVENTS** = .../events-overview (2026-09-14)
- **G-LIMITS** = .../limits (2026-09-03)

Other Google pages:

- Fetched, nothing used: .../guides/downloads (client libraries list).
- **CAL-EVENTS** = https://developers.google.com/workspace/calendar/api/v3/reference/events
  (2026-07-07). Read for `summary` and `conferenceData` only.
- **HELP-TRANSCRIPTS** = https://support.google.com/meet/answer/12849897 ("Use Transcripts with
  Google Meet", Meet Help; no date shown).
- **ADMIN-TRANSCRIPTS** = https://knowledge.workspace.google.com/admin/meet/turn-meeting-transcription-on-or-off
  (reached from HELP-TRANSCRIPTS's link /a/answer/12076932; 2026-11-10).
- **ADMIN-AUTO** = https://knowledge.workspace.google.com/admin/meet/choose-automatic-meeting-artifact-settings-for-your-organization
  (linked from ADMIN-TRANSCRIPTS; 2026-11-10).
- **WS-POLICY** = https://developers.google.com/workspace/workspace-api-user-data-developer-policy
  (2026-09-03).
- **UD-POLICY** = https://developers.google.com/terms/api-services-user-data-policy ("Last
  updated February 15, 2024").
- **[source: PB-TIMESTAMP]** = https://protobuf.dev/reference/protobuf/google.protobuf/#timestamp
  (the target of every "Timestamp format" link on the reference pages).
- **[source: AIP-193]** = https://google.aip.dev/193 (general Google error design; see google-auth.md C.2).

Not fetched: https://support.google.com/a/answer/12083948 (tried for the admin transcription
page) redirected to the Admin Help home page, so ADMIN-TRANSCRIPTS was reached through the Help
Center's own link instead. `.../meet/api/release-notes` and `.../meet/api/guides/troubleshoot` were guessed
URLs and returned 404. The reference pages for `SignedinUser`, `AnonymousUser` and `PhoneUser`
as separate pages returned 404; their definitions are on PA.

Tags: **[doc: X]** = stated on page X; **[example: X]** = visible only in an example on page X;
**[not stated]** = nowhere in the pages above. **QUESTION:** marks an open point that matters
for the design; **CONFLICT** quotes two Google statements that disagree.

---

## Headline points (read first)

1. **Which conferences one user's `list` returns is contradicted on the same page.** "If you're
   a meeting space owner or participant, you can call the `get` and `list` methods to retrieve
   conference records." vs "Note: The `list` method only returns conferences where you're the
   meeting organizer." [doc: G-CONF]. QUESTION below.
2. **There is no documented "ended since" query.** `conferenceRecords.list` is "ordered by start
   time and in descending order" [doc: CR-LIST]; `end_time` is filterable, but the only `end_time`
   example is `end_time IS NULL`, and the only comparison example is on `start_time`
   [doc: CR-LIST]. Whether `end_time>="..."` works, and how it treats ongoing conferences, is
   **[not stated]**. CR-LIST lists no `orderBy` parameter. QUESTION below.
3. **Transcript entries are deleted 30 days after the conference ends**, and so is the conference
   record itself: "The resource is deleted 30 days after the conference ends." [doc: CR,
   `expireTime`]; "Transcript entries provided by the Meet REST API are deleted 30 days after the
   conference ends." [doc: G-ARTIFACTS]
4. **Small pages.** Transcript entries default to 10 per page, maximum 100; transcripts 10/100;
   conference records 25/100; participants 100/250. [doc: TE-LIST, TR-LIST, CR-LIST, PA-LIST]
5. **Readiness is the transcript's `state`.** `ENDED`: "This transcript session has ended, but
   the transcript file hasn't been generated yet."; `FILE_GENERATED`: "Transcript file is
   generated and ready to download." [doc: TR] How long that takes is given only by the Help
   Center: as a rule within a few hours of the meeting, and up to 24 hours (paraphrased)
   [doc: ADMIN-TRANSCRIPTS].
6. **No meeting title exists anywhere in the Meet API** (section F).
7. **Quota for a service account.** "API calls by a service account are considered to be using a
   single account." [doc: G-LIMITS] Per user per project: 600 reads a minute. How that applies
   under domain-wide delegation is **[not stated]** (G).

---

## A. Service, version, scopes

- Service `meet.googleapis.com`; service endpoint `https://meet.googleapis.com`; discovery
  document `https://meet.googleapis.com/$discovery/rest?version=v2`. [doc: REST]
- Version path `v2`, e.g. `GET https://meet.googleapis.com/v2/conferenceRecords`. [doc: CR-LIST]
- "The URL uses gRPC Transcoding syntax." [doc: every method page]
- Every read method used here needs one of [doc: CR-LIST, CR-GET, TR-LIST, TR-GET, TE-LIST,
  TE-GET, PA-LIST, PA-GET; same in DISCOVERY]:
  - `https://www.googleapis.com/auth/meetings.space.created`
  - `https://www.googleapis.com/auth/meetings.space.readonly`
- `spaces.get` also accepts `https://www.googleapis.com/auth/meetings.space.settings`. [doc: SP-GET]
- Scope meanings and sensitivity [doc: G-AUTH]:

| Scope | Description (quoted) | Usage |
|---|---|---|
| `meetings.space.settings` | "Edit and see the settings for all of your Google Meet calls." | Non-sensitive |
| `meetings.space.created` | "Allow apps to create, modify, and read metadata about meeting spaces created by your app." | Sensitive |
| `meetings.space.readonly` | "Allow apps to read metadata about any meeting space the user has access to." | Sensitive |
| `drive.readonly` | "Allow apps to download recording and transcript files from Google Drive API." | Restricted |
| `drive.meet.readonly` (a Drive scope) | "View Drive files created or edited by Google Meet." | Restricted |

- "If the app you're using to view conferences only has the `meetings.space.created` OAuth
  scope, you can only view conferences for the spaces the app created." [doc: G-CONF] "Get or
  list artifacts from conferences created by other apps" needs `meetings.space.readonly`.
  [doc: G-SPACES-OV, "OAuth scopes by use case"]
- Restricted scopes: "If you store restricted scope data on servers (or transmit), then you
  must go through a security assessment." [doc: G-AUTH] Transcript entries through
  `meetings.space.readonly` are "Sensitive" per the table; the Doc through Drive is "Restricted".
- Authentication: "For Meet REST API, you can only authenticate using user authentication."
  Domain-wide delegation "is therefore considered user authentication. Any capability that
  requires user authentication can use domain-wide delegation." [doc: G-AUTH]
- "The Meet REST API isn't intended for performance tracking or user evaluation within your
  domain. Meet data shouldn't be collected for this purpose." [doc: G-OVERVIEW]
- Timestamps: every `Timestamp format` field is protobuf Timestamp, "encoded as a string in
  the RFC 3339 format: "`{year}-{month}-{day}T{hour}:{min}:{sec}[.{frac_sec}]Z`"", fractional
  seconds optional up to 9 digits; "A proto3 JSON serializer should always use UTC (as indicated
  by "Z")". [source: PB-TIMESTAMP] DISCOVERY types them `string`, `format: google-datetime`.
  How many fractional digits Meet actually emits is **[not stated]**.

## B. `conferenceRecords.list` and the ConferenceRecord

`GET https://meet.googleapis.com/v2/conferenceRecords`; request body empty. [doc: CR-LIST]

"Lists the conference records. By default, ordered by start time and in descending order."
[doc: CR-LIST] G-CONF: "ordered by `startTime` in descending order". CR-LIST lists no parameter
that changes the order.

| Parameter | What the page says |
|---|---|
| `pageSize` | integer, optional. "If unspecified, at most 25 conference records are returned. The maximum value is 100; values above 100 are coerced to 100. Maximum might change in the future." "The service might return fewer than this value." |
| `pageToken` | string, optional. "Page token returned from previous List Call." |
| `filter` | string, optional. "User specified filtering condition in EBNF format." |

**The `filter` grammar, all the page gives** [doc: CR-LIST; DISCOVERY has the same text]:

- Filterable fields: `space.meeting_code`, `space.name`, `start_time`, `end_time`.
- Examples, the only ones given:
  - `space.name = "spaces/NAME"`
  - `space.meeting_code = "abc-mnop-xyz"`
  - `start_time>="2024-01-01T00:00:00.000Z" AND start_time<="2024-01-02T00:00:00.000Z"`
  - `end_time IS NULL`
- Operators seen in the examples: `=`, `>=`, `<=`, `AND`, `IS NULL`. A full operator list
  (`>`, `<`, `!=`, `OR`, `NOT`, `IS NOT NULL`) is **[not stated]**; "EBNF format" links to the
  Wikipedia article on EBNF, not to a grammar.
- Timestamp literal: quoted, RFC 3339 with milliseconds and `Z` in the example. Whether
  unquoted or offset forms are accepted is **[not stated]**.
- **CONFLICT on filter syntax across Meet pages.** The reference uses snake_case fields and
  quoted timestamps (`start_time>="2024-01-01T00:00:00.000Z"`) [doc: CR-LIST]; the participants
  guide uses camelCase and unquoted timestamps (`earliestStartTime < 2023-10-01T15:01:23Z`,
  `latestEndTime IS NULL`) [doc: G-PARTICIPANTS], while PA-LIST and DISCOVERY name the
  participant fields `earliest_start_time` and `latest_end_time`. QUESTION below.
- What a malformed filter returns is **[not stated]**.
- **[cairn]** A source of one meeting sends `start_time>="<RFC 3339>" AND
  space.meeting_code = "abc-mnop-xyz"`, the reference's spelling, not checked against the real
  service; a 400 to it reads nothing. It first resolves the code with `spaces.get` on
  `spaces/{meetingCode}` (SP-GET) and reads only records whose `space` equals that space's
  `name`; that a record's `space` has that form (F) is not checked against the real service.

**Response** [doc: CR-LIST]:

```
{
  "conferenceRecords": [ { object (ConferenceRecord) } ],
  "nextPageToken": string
}
```

`nextPageToken`: "Token to be circulated back for further List call if current List does NOT
include all the Conferences. Unset if all conferences have been returned." Whether an empty
result has `conferenceRecords` absent or `[]` is **[not stated]**.

**ConferenceRecord** [doc: CR; "Single instance of a meeting held in a space."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `name` | string | "Identifier. Resource name of the conference record. Format: `conferenceRecords/{conferenceRecord}` where `{conferenceRecord}` is a unique ID for each instance of a call within a space." |
| `startTime` | string (Timestamp) | "Output only. Timestamp when the conference started. Always set." |
| `endTime` | string (Timestamp) | "Output only. Timestamp when the conference ended. Set for past conferences. Unset if the conference is ongoing." |
| `expireTime` | string (Timestamp) | "Output only. Server enforced expiration time for when this conference record resource is deleted. The resource is deleted 30 days after the conference ends." |
| `space` | string | "Output only. The space where the conference was held." |

[example: G-OVERVIEW] `conferenceRecords/kRyYx8b7vNDsLpR1tG_cNjFUQBoBRhHIMoGJAJkBCQ`. The format
of `space` (presumably `spaces/{space}`) is **[not stated]** on CR.

`conferenceRecords.get`: `GET https://meet.googleapis.com/v2/{name=conferenceRecords/*}`; `name`
"Required. Resource name of the conference." Returns a ConferenceRecord. [doc: CR-GET]

**Which conferences one user sees** (quoted):

- G-CONF, intro: "If you're a meeting space owner or participant, you can call the `get` and
  `list` methods to retrieve conference records."
- G-CONF, "List all conferences": "The method returns a list of past conferences ... They're
  filtered to the conference organizer and ordered by `startTime` in descending order."
- G-CONF, note: "The `list` method only returns conferences where you're the meeting
  organizer."
- G-ARTIFACTS and G-PARTICIPANTS repeat "If you're a meeting space owner or participant, you can
  call the `get` and `list` methods" for artifacts and participants.
- G-EVENTS: "Calendar invitees and other participants invited to a conference can only receive
  the following events: `google.workspace.meet.conference.v2.started` and
  `google.workspace.meet.transcript.v2.fileGenerated`."
- G-OVERVIEW defines "Meeting organizer" as "The user that created the meeting space ... There
  can only be one meeting organizer."
- **CONFLICT.** Owner-or-participant vs organizer-only for `list`. QUESTION below.
- **CONFLICT on past vs ongoing.** G-CONF says `list` "returns a list of past conferences", while
  CR-LIST offers `end_time IS NULL` and CR says `endTime` is "Unset if the conference is ongoing".
  [doc: G-CONF, CR-LIST, CR] Whether ongoing conferences appear without a filter is
  **[not stated]**. QUESTION below.

## C. Transcripts: `conferenceRecords.transcripts.list` and `get`

`GET https://meet.googleapis.com/v2/{parent=conferenceRecords/*}/transcripts`; `parent`
"Required. Format: `conferenceRecords/{conferenceRecord}`". [doc: TR-LIST]

- "Lists the set of transcripts from the conference record. By default, ordered by start time
  and in ascending order." [doc: TR-LIST]
- `pageSize`: "If unspecified, at most 10 transcripts are returned. The maximum value is 100;
  values above 100 are coerced to 100." `pageToken`: "Page token returned from previous List
  Call." [doc: TR-LIST]
- No `filter`: "Unlike `conferenceRecords`, `participants`, and `participantSessions` resources,
  you cannot filter query results for `recordings`, `transcripts`, `transcripts.entries`, and
  `smartNotes` resources." [doc: G-ARTIFACTS]
- Response: `{"transcripts": [ { object (Transcript) } ], "nextPageToken": string}`;
  `nextPageToken` "Unset if all transcripts are returned." [doc: TR-LIST]

`get`: `GET https://meet.googleapis.com/v2/{name=conferenceRecords/*/transcripts/*}`; `name`
"Required. Resource name of the transcript."; returns a Transcript. [doc: TR-GET]

**Transcript** [doc: TR; "Metadata for a transcript generated from a conference. It refers to
the ASR (Automatic Speech Recognition) result of user's speech during the conference."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `name` | string | "Output only. Resource name of the transcript. Format: `conferenceRecords/{conferenceRecord}/transcripts/{transcript}`, where `{transcript}` is a 1:1 mapping to each unique transcription session of the conference." |
| `state` | enum State | "Output only. Current state." |
| `startTime` | string (Timestamp) | "Output only. Timestamp when the transcript started." |
| `endTime` | string (Timestamp) | "Output only. Timestamp when the transcript stopped." |
| `docsDestination` | object DocsDestination | "Output only. Where the Google Docs transcript is saved." In a one-member union: "At most one of the fields will be set in a response". |

State [doc: TR]:

| Value | Meaning (quoted) |
|---|---|
| `STATE_UNSPECIFIED` | "Default, never used." |
| `STARTED` | "An active transcript session has started." |
| `ENDED` | "This transcript session has ended, but the transcript file hasn't been generated yet." |
| `FILE_GENERATED` | "Transcript file is generated and ready to download." |

DocsDestination [doc: DOCSDEST]:

| Field | Type | Description (quoted) |
|---|---|---|
| `document` | string | "Output only. The document ID for the underlying Google Docs transcript file. For example, "1kuceFZohVoCh6FulBHxwy6I15Ogpc4hP". Use the `documents.get` method of the Google Docs API ... to fetch the content." |
| `exportUri` | string | "Output only. URI for the Google Docs transcript file. Use `https://docs.google.com/document/d/{$DocumentId}/view` to browse the transcript in the browser." |

**When a transcript appears, and how an unfinished one looks:**

- One conference can have several transcripts: each start of Transcripts makes a file of its
  own (paraphrased) [doc: HELP-TRANSCRIPTS]; `{transcript}` maps "1:1 ... to each unique
  transcription session" [doc: TR].
- "Meet has access to the transcript ID once the transcript data is generated." [doc: G-ARTIFACTS]
- The lifecycle: "The conference has ended (`STATE=ENDED`) but the artifacts haven't been
  generated yet. ... You must poll the artifact endpoint until the artifact object `STATE` reads
  `FILE_GENERATED`." "Once ready, a URI to the artifact object is populated". [doc: G-SPACES-OV]
- Delay: G-OVERVIEW says artifacts are "usually ready to be fetched soon after a conference
  ends"; ADMIN-TRANSCRIPTS says (paraphrased) organizers usually have
  transcripts a few hours after the meeting, and that it can take up to 24 hours; HELP-TRANSCRIPTS says (paraphrased) that a longer
  meeting's transcript takes longer to process. **CONFLICT** in tone; no API page gives a number. QUESTION
  below.
- Whether `docsDestination` is absent while `STARTED`/`ENDED` is implied by "Once ready, a URI ...
  is populated" [doc: G-SPACES-OV] but not stated on TR **[not stated]**.
- Whether a transcript in `STARTED` or `ENDED` is listed at all, versus appearing only once
  generated ("once the transcript data is generated"), is **[not stated]**.
- Whether entries can be read before `FILE_GENERATED` is **[not stated]**. QUESTION below.
- A conference with no transcript: what `transcripts.list` returns is **[not stated]**.
- "It's possible a user might delete the transcript file from Drive, yet the unique name is still
  returned in Meet." [doc: G-ARTIFACTS]
- "Transcripts operate independently of recordings"; "When all participants leave the meeting,
  the transcript stops automatically." [doc: G-ARTIFACTS]

## D. Transcript entries: `conferenceRecords.transcripts.entries.list`

`GET https://meet.googleapis.com/v2/{parent=conferenceRecords/*/transcripts/*}/entries`; `parent`
"Required. Format: `conferenceRecords/{conferenceRecord}/transcripts/{transcript}`".
[doc: TE-LIST]

- "Lists the structured transcript entries per transcript. By default, ordered by start time and
  in ascending order." [doc: TE-LIST]
- `pageSize`: "If unspecified, at most 10 entries are returned. The maximum value is 100; values
  above 100 are coerced to 100. Maximum might change in the future." `pageToken`: "Page token
  returned from previous List Call." No filter. [doc: TE-LIST, G-ARTIFACTS]
- Response: `{"transcriptEntries": [ { object (TranscriptEntry) } ], "nextPageToken": string}`;
  `nextPageToken` "Unset if all entries are returned." [doc: TE-LIST]
- Whether page tokens expire, and whether the order is stable when two entries share a
  `startTime`, are **[not stated]**.

`get`: `GET https://meet.googleapis.com/v2/{name=conferenceRecords/*/transcripts/*/entries/*}`;
`name` "Required. Resource name of the `TranscriptEntry`."; carries the same not-match note.
[doc: TE-GET]

**TranscriptEntry** [doc: TE; "Single entry for one user's speech during a transcript session."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `name` | string | "Output only. Resource name of the entry. Format: "conferenceRecords/{conferenceRecord}/transcripts/{transcript}/entries/{entry}"" |
| `participant` | string | "Output only. Refers to the participant who speaks." |
| `text` | string | "Output only. The transcribed text of the participant's voice, at maximum 10K words. Note that the limit is subject to change." |
| `languageCode` | string | "Output only. Language of spoken text, such as "en-US". IETF BCP 47 syntax" |
| `startTime` | string (Timestamp) | "Output only. Timestamp when the transcript entry started." |
| `endTime` | string (Timestamp) | "Output only. Timestamp when the transcript entry ended." |

- `participant` is a Participant resource name: "Each transcript entry is connected to a
  `participant` name in the conference. To retrieve participant details, see Get details about a
  participant." [doc: G-ARTIFACTS] Its exact format (`conferenceRecords/{c}/participants/{p}`) is
  shown on PA, not on TE.
- **30-day deletion:** "Transcript entries provided by the Meet REST API are deleted 30 days after
  the conference ends." [doc: G-ARTIFACTS, "Artifact retention"]; "Transcript entry data is
  available for 30 days after the conference ends." [doc: G-ARTIFACTS]; "available for 30 days
  after the conference ends" [doc: G-OVERVIEW]. The Docs file itself is "retained according to
  Drive rules" [doc: G-ARTIFACTS].
- **Entries vs the Doc. CONFLICT on the causes.** TE-LIST and TE-GET: "The transcript entries
  returned by the Google Meet API might not match the transcription found in the Google Docs
  transcript file. This can occur when 1) we have interleaved speakers within milliseconds, or 2)
  the Google Docs transcript file is modified after generation." G-ARTIFACTS gives only the second
  cause: "This occurs when the transcript file is modified after generation." QUESTION below.

## E. Participants: `conferenceRecords.participants.list` and `get`

`GET https://meet.googleapis.com/v2/{parent=conferenceRecords/*}/participants`; `parent`
"Required. Format: `conferenceRecords/{conferenceRecord}`". [doc: PA-LIST]

- "By default, ordered by join time and in descending order." [doc: PA-LIST]; "ordered by
  `earliestStartTime` in descending order" [doc: G-PARTICIPANTS].
- "This API supports `fields` as standard parameters like every other API. However, when the
  `fields` request parameter is omitted, this API defaults to `'participants/*, nextPageToken'`."
  [doc: PA-LIST]
- `pageSize`: "If unspecified, at most 100 participants are returned. The maximum value is 250;
  values above 250 are coerced to 250." `pageToken`. [doc: PA-LIST]
- `filter`: "Optional. User specified filtering condition in EBNF format." Filterable fields:
  `earliest_start_time`, `latest_end_time`; "For example, `latest_end_time IS NULL` returns
  active participants in the conference." [doc: PA-LIST] (Guide's camelCase form: see the
  CONFLICT in B.)
- Response: `{"participants": [...], "nextPageToken": string, "totalSize": integer}`;
  `totalSize` "Total, exact number of `participants`. By default, this field isn't included in
  the response." [doc: PA-LIST]

`get`: `GET https://meet.googleapis.com/v2/{name=conferenceRecords/*/participants/*}`; `name`
"Required. Resource name of the participant."; returns a Participant. [doc: PA-GET]

**Participant** [doc: PA; "User who attended or is attending a conference."]:

| Field | Type | Description (quoted) |
|---|---|---|
| `name` | string | "Output only. Resource name of the participant. Format: `conferenceRecords/{conferenceRecord}/participants/{participant}`" |
| `earliestStartTime` | string (Timestamp) | "Output only. Time when the participant first joined the meeting." |
| `latestEndTime` | string (Timestamp) | "Output only. Time when the participant left the meeting for the last time. This can be null if it's an active meeting." |
| `signedinUser` / `anonymousUser` / `phoneUser` | object | union: "At most one of the fields will be set in a response" |

- **SignedinUser**: "a) An individual joining from a personal computer, mobile device, or through
  companion mode. b) A robot account used by conference room devices." Fields: `user` ("Unique
  ID for the user. Interoperable with Admin SDK API and People API. Format: `users/{user}`"),
  `displayName` ("For a personal device, it's the user's first name and last name. For a robot
  account, it's the administrator-specified device name. For example, "Altostrat Room"."). [doc: PA]
- **AnonymousUser**: `displayName`: "User provided name when they join a conference anonymously."
  [doc: PA]
- **PhoneUser**: `displayName`: "Partially redacted user's phone number when calling." [doc: PA]
- "There's one `participants` resource for each person." [doc: G-PARTICIPANTS] A person who
  rejoins gets new participant sessions, not a new participant ("If the participant joins the same
  call multiple times from the same participant-device pair, they're each assigned unique session
  IDs.") [doc: G-PARTICIPANTS]
- **CONFLICT on the People API id.** G-PARTICIPANTS: "Extract the ID of the person from the
  `participant` resource using the trailing component of the path. For example, if the
  `participant` resource value is `conferenceRecords/abc-123/participants/12345` the ID for the
  People API is `12345`." PA says the People-API-interoperable ID is `signedinUser.user`
  (`users/{user}`). Also: "For privacy reasons, profile information might not be available for
  all participants." [doc: G-PARTICIPANTS] QUESTION below.
- Participant data is reachable "for up to 30 days after the conference" [doc: G-SPACES-OV].

## F. `spaces.get`, and the meeting title

`GET https://meet.googleapis.com/v2/{name=spaces/*}`; `name` "Format: `spaces/{space}` or
`spaces/{meetingCode}`". [doc: SP-GET] A ConferenceRecord's `space` can be passed here (its format
being `spaces/{space}` is **[not stated]** on CR).

**Space** [doc: SP; "Virtual place where conferences are held."]:

| Field | Type | Description (quoted, shortened) |
|---|---|---|
| `name` | string | "Immutable. Resource name of the space. Format: `spaces/{space}`." `{space}` is "a unique, server-generated ID and is case sensitive. For example, `jQCFfuBOdN5z`." |
| `meetingUri` | string | "Output only. URI used to join meetings consisting of `https://meet.google.com/` followed by the `meetingCode`." |
| `meetingCode` | string | "Output only. Type friendly unique string used to join the meeting. Format: `[a-z]+-[a-z]+-[a-z]+`." Max 128 characters. |
| `config` | object SpaceConfig | "Configuration pertaining to the meeting space." (`accessType`, `entryPointAccess`, `moderation`, `moderationRestrictions`, `attendanceReportGenerationType`, `artifactConfig`) |
| `activeConference` | object | `conferenceRecord`: "Reference to 'ConferenceRecord' resource." |
| `phoneAccess[]`, `gatewaySipAccess[]` | objects | dial-in and SIP details |

- `config.artifactConfig.transcriptionConfig.autoTranscriptionGeneration` (`ON`/`OFF`): "Defines
  whether the content of a meeting is automatically transcribed when someone with the privilege
  to transcribe joins the meeting." [doc: SP]
- "A `meetingCode` shouldn't be stored long term as it can become dissociated from a meeting
  space and can be reused for different meeting spaces in the future. Generally, a `meetingCode`
  expires 365 days after last use." [doc: SP-GET]
- Who may call it [doc: G-SPACES-OV, "Meeting space method permissions", read from the icons]:
  `get` = owners yes, participants yes, others yes; "with settings" = owners only; "with active
  conference" = owners and participants. What a non-owner's `config` looks like (absent, partial)
  is **[not stated]**.

**Meeting title:** no Meet resource read here has a title or subject field: ConferenceRecord
(`name`, `startTime`, `endTime`, `expireTime`, `space`), Space (above), Transcript, Participant
[doc: CR, SP, TR, PA]. No Meet page mentions a title **[not stated]**. What the docs do state:

- Calendar's Event has `summary`: "Title of the event." [doc: CAL-EVENTS]
- `conferenceData.conferenceId` for `hangoutsMeet`: "ID is the 10-letter meeting code, for example
  `aaa-bbbb-ccc`." [doc: CAL-EVENTS]
- The transcript Doc's file name carries the title in the Help Center's example: "Team Sync
  (2022-9-13 at 10:00 PST) - Transcript" [example: ADMIN-TRANSCRIPTS].
- When the meeting belongs to a Google Calendar event and is held at the scheduled time, the
  Calendar event links the transcript too (paraphrased). [doc: ADMIN-TRANSCRIPTS]
- No page describes a link from a ConferenceRecord to a Calendar event **[not stated]**, and the
  meeting code may be reused (above). QUESTION below.

## G. Quotas and limits

[doc: G-LIMITS]

| Kind | Per minute per project | Per minute per user per project |
|---|---|---|
| Read requests | 6000 | 600 |
| Write requests | 1000 | 100 |
| Reduced write requests (`spaces.create`) | 100 | 10 |

- "Read requests are calls to any method that retrieves data from Meet REST API, such as
  `spaces.get`, `conferenceRecords.get`, or `conferenceRecords.recordings.list`."
- "If you exceed a quota, you'll generally receive a `429: Too many requests` HTTP status code
  response. If this happens, you should use an exponential backoff algorithm and try again
  later." The error body and `reason` are **[not stated]** for Meet (see google-auth.md C.2 for
  the general forms).
- "Provided you stay within the per-minute quotas, there's no limit to the number of requests you
  can make per day."
- Backoff: wait `min(((2^n)+random_number_milliseconds), maximum_backoff)`, with
  `random_number_milliseconds` "less than or equal to 1,000" and `maximum_backoff` "typically 32
  or 64 seconds"; "At some point, clients should be prevented from retrying indefinitely."
- Whether a 429 carries `Retry-After` is **[not stated]**.
- "API calls by a service account are considered to be using a single account." Whether calls
  made by a service account impersonating several users through domain-wide delegation count per
  impersonated user or as one account is **[not stated]**. QUESTION below.
- **Charges:** "All standard use of the Google Meet API is available at no additional cost.
  Exceeding the quota request limits is planned to incur charges to your Google Cloud billing
  account later in 2026." The page was last updated 2026-09-03; whether charging has started by
  2026-11-13 is **[not stated]**. QUESTION below.

## H. Errors

No Meet page documents error responses: there is no Meet errors page (the guessed
`.../guides/troubleshoot` is 404), and no method page lists error codes **[not stated]**.

- A record, transcript or entry that does not exist: status and body **[not stated]**.
- One the caller may not see: **[not stated]** for Meet. The general rule [source: AIP-193]:
  "If the user does not have permission to access the resource or parent, regardless of whether
  or not it exists, the service must error with `PERMISSION_DENIED` (HTTP 403)", else `NOT_FOUND`
  (HTTP 404). Whether Meet follows it is **[not stated]**.
- Entries after their 30 days: "deleted" [doc: G-ARTIFACTS]; whether `entries.list` then returns
  404, 403 or an empty list, and whether the Transcript itself is still listed, is
  **[not stated]**. The ConferenceRecord is deleted at the same 30 days (`expireTime`) [doc: CR],
  so its `get` after that presumably fails, but the answer is **[not stated]**.
- Transcript file deleted from Drive: "the unique name is still returned in Meet" [doc:
  G-ARTIFACTS]; whether entries still list is **[not stated]**.
- QUESTION below: the status and body for each of these cases.

## I. On the customer's side

- **Editions. CONFLICT: three lists.**
  - HELP-TRANSCRIPTS ("Learn which Google Workspace editions have Transcripts"): Business
    Standard, Business Plus, Enterprise Starter, Enterprise Standard, Enterprise Plus, Teaching and
    Learning Upgrade, Education Plus, Workspace Individual.
  - ADMIN-TRANSCRIPTS ("Supported editions for this feature"): Business Standard, Business Plus,
    Enterprise Standard, Enterprise Plus, Education Plus.
  - ADMIN-AUTO (automatic artifacts): Business Plus, Enterprise Standard, Enterprise Plus,
    Education Plus, Enterprise Essentials, Enterprise Essentials Plus.
  - QUESTION below.
- **On by default, not automatic:** meeting transcripts are on by default in every Workspace
  edition but Google Workspace for Education with a student license (paraphrased)
  [doc: HELP-TRANSCRIPTS];
  the feature is on by default but does not run by itself in each meeting: a person in the
  meeting has to start transcription (paraphrased). [doc: ADMIN-TRANSCRIPTS]
- **Making it automatic:**
  - Admin: Admin console > Apps > Google Workspace > Google Meet > Meet video settings >
    "Automatic transcription" > "Meetings are transcribed by default"; the settings reach only
    meetings made afterwards, not recurring meetings created before the setting was switched on
    (paraphrased). [doc: ADMIN-AUTO]
  - Host, per meeting: when creating a meeting, Video call options > Meeting records, then
    Transcribe the meeting; once switched on, these features still start only when the host or
    a co-host joins the meeting on the web (paraphrased). [doc: HELP-TRANSCRIPTS]
  - API: `autoTranscriptionGeneration` (F), set with `meetings.space.settings`; "Meeting
    organizers, but not co-hosts, can pre-configure auto-recording, auto-transcripts". [doc:
    G-SPACES-CONFIG]
  - Admins may require participant consent before transcription; that setting is off by
    default. [doc: HELP-TRANSCRIPTS]
- **Languages:** English, French, German, Italian, Japanese, Korean, Portuguese, Spanish [doc:
  HELP-TRANSCRIPTS]. Automatic transcripts use the host's language: with the host's Meet language
  set to French and automatic transcription on, the transcript is in French (paraphrased).
  [doc: ADMIN-AUTO]
- **Where the file goes. CONFLICT on the folder.** HELP-TRANSCRIPTS (paraphrased): the file is
  saved to the meeting organizer's Google Drive, in the folder `Google Meet`, with a sub-folder per
  meeting, and from July 2026 an existing folder `Meet Recordings` is renamed by itself to
  `Legacy Meet Recordings`. ADMIN-TRANSCRIPTS (updated 2026-11-10, paraphrased): organizers
  also find transcripts in the folder `Meet Recordings` in Drive. Both agree it is the
  organizer's Drive; G-ARTIFACTS: "Meet saves recordings, transcripts, and smart notes to the
  meeting organizer's Google Drive." QUESTION below.
- Needs enough Drive storage in both the organization and the host's Drive. [doc:
  HELP-TRANSCRIPTS]
- **How long:** see C (a few hours as a rule, up to 24 hours [doc: ADMIN-TRANSCRIPTS]).
- Only speech is transcribed: a transcript holds what was said in the meeting and nothing else
  (paraphrased). [doc: HELP-TRANSCRIPTS]
- A change to an admin setting can take up to 24 hours. [doc: ADMIN-TRANSCRIPTS, ADMIN-AUTO]

## J. Policy (quoted, not interpreted)

From WS-POLICY (page title "Google Workspace user data and developer policy"):

- Approved Meet use cases include: "Applications that use information from Meet to provide
  reporting or monitoring services for the benefit of users (for example, an app providing
  meeting or speaking insights)."
- Not allowed: "Applications that monitor or distribute Meet user data, content, or metadata
  without legal authorization or without consent."
- Limited use, scope: "These requirements apply to data derived from both Sensitive and
  Restricted scopes."
- "Limit your use of data to providing or improving your appropriate use case or features that are
  visible and prominent in the requesting application's user interface."
- "Transfers of data are not allowed, except:" and the first exception: "To provide or improve
  your appropriate use case or user-facing features that are visible and prominent in the
  requesting application's user interface and only with the user's consent;" (the others: security
  purposes, applicable laws, a merger with the user's prior consent).
- Prohibited: "Transferring, selling, or using user data to create, train, or improve a machine
  learning or artificial intelligence model beyond that specific user's personalized model for the
  appropriate use case or user-facing feature."
- "Note: Remember, our Terms of Service prohibits the scraping, building databases (including
  databases for model training purposes), or otherwise creating permanent copies of Google User
  data. This includes keeping cached copies longer than permitted by the cache header."
- "Treat all user data securely in transit and at rest."
- "Applications accessing Restricted Scopes must demonstrate that they adhere to certain security
  practices." Then "Required security measures include:" encrypting user data "At rest on your
  systems", "Keeping user data and credentials, specifically tokens such as OAuth access and refresh
  tokens, encrypted at rest", and "Protecting against prompt injection techniques by either using
  Google Cloud Platform's Model Armor or other prompt injection protection."
- Restricted Meet scopes are those that "Read, modify, or manage the real-time processing of audio
  and video from participants in the meeting."
- Disclosure: "The use of information received from Google Workspace scopes will adhere to the
  Google User Data Policy, including the Limited Use requirements." (suggested wording)

From UD-POLICY ("Google API Services User Data Policy"):

- "Note: If your app is only used by users within your own domain, then these requirements do not
  apply." (said of the "Additional Requirements for Specific API Scopes").
- Limited Use "requirements apply to the raw data obtained from the scopes and data aggregated,
  anonymized, or derived from them."
- "Don't allow humans to read the data, unless:" and the first exception: "You first obtained the
  user's affirmative agreement to view specific messages, files, or other data".
- "Your privacy policy and in-product privacy notifications must thoroughly disclose the manner in
  which your application accesses, uses, stores, or shares Google user data."

From G-OVERVIEW: example uses include "Run transcriptions and recordings through AI models to
generate coaching and sales effectiveness analysis." and "Run transcription data through AI models to
generate the correct next steps."; and "Note: The Meet REST API isn't intended for performance
tracking or user evaluation within your domain. Meet data shouldn't be collected for this purpose."

---

## Open questions for the connector

1. QUESTION: Does `conferenceRecords.list` return conferences the user only took part in, or only
   ones they organized? G-CONF says both "meeting space owner or participant" and "only returns
   conferences where you're the meeting organizer" (B, headline 1). This decides whether one
   user's transcripts can be found by listing at all.
   **[cairn]** cairn reads the meetings `list` returns for the person, whichever they are, and
   its guide says so in those words, without promising the meetings someone only attended.
2. QUESTION: Is `end_time>="<RFC 3339>"` a valid filter, and does it skip ongoing conferences?
   Only `end_time IS NULL` and a `start_time` range are exemplified, and results are ordered by
   start time descending with no order parameter (B, headline 2).
   **[cairn]** cairn filters only on the exemplified form, `start_time>="<RFC 3339>"`, a day
   before the start of its last finished pull, sorts what it gets by end time itself, and
   passes over a record with no `endTime` until a later pull.
3. QUESTION: Does `list` without a filter include ongoing conferences? G-CONF says it "returns a
   list of past conferences"; CR says `endTime` is "Unset if the conference is ongoing" (B).
   **[cairn]** cairn always sends a filter, and treats a listed record without `endTime` as
   going on, whatever the answer.
4. QUESTION: Which filter spelling does the server accept: snake_case with quoted timestamps
   (reference) or camelCase with unquoted timestamps (participants guide) (B)?
   **[cairn]** The reference's: snake_case, the timestamp quoted, UTC with milliseconds and
   `Z`, and the same filter beside every `pageToken`.
5. QUESTION: Are transcript entries readable before the transcript reaches `FILE_GENERATED`, and
   are transcripts in `STARTED` or `ENDED` listed at all (C)?
   **[cairn]** cairn reads entries only in `FILE_GENERATED`, so it never writes a part of a
   transcript; a transcript in any other state is waited for.
6. QUESTION: How long after a conference ends does a transcript reach `FILE_GENERATED`? "soon
   after" (G-OVERVIEW) vs a few hours, up to 24 hours (ADMIN-TRANSCRIPTS) (C).
   **[cairn]** A meeting is looked at by every pull until `wait_hours` after it ended (48
   unless set, from 1 to 168), twice the longest delay the pages give; one not generated by
   then is reported once and not read.
7. QUESTION: What do `list` and `get` answer (status, body) for a record or transcript that does
   not exist, that the caller may not see, or whose entries passed their 30 days (H)?
   **[cairn]** A 404 for one meeting (its transcripts, participants or record) or one
   transcript's entries is a skip, not asked about again; a 404 from `spaces.get` leaves the
   title without a meeting code; 401 and 403 that are not rate limits fail the pull with what
   to check. A 404 to `conferenceRecords.list` itself fails the pull: base_url is wrong.
8. QUESTION: With domain-wide delegation, does the 600 reads per minute per user apply per
   impersonated user, or to the service account as "a single account" (G)?
   **[cairn]** One source reads for one person and makes at most 2,000 requests a pull; a
   rate limit is waited out four times, then the next run goes on.
9. QUESTION: Has charging for requests over quota started? G-LIMITS (updated 2026-09-03) says it
   is "planned ... later in 2026" (G).
10. QUESTION: Where does a meeting's title come from? No Meet resource has one; Calendar has
    `summary`, but no page links a ConferenceRecord to an event, and meeting codes are reused (F).
    **[cairn]** None is read: a page is named "Meet <meeting code>, <start in UTC>", or
    "Meet, <start in UTC>" when `spaces.get` gives no code, so a reused code still gets a
    page a meeting.
11. QUESTION: Do entries differ from the Doc only when the Doc was edited, or also with
    "interleaved speakers within milliseconds"? The reference gives both causes, the guide one (D).
12. QUESTION: Which id identifies a signed-in participant for the People API: `signedinUser.user`
    or the trailing component of the participant's `name` (E)?
    **[cairn]** Neither: cairn writes the display name Meet gives, never an id or an address,
    and a display name holding "@" counts as no name.
13. QUESTION: Which Workspace editions can transcribe? Three Google pages give three lists (I).
14. QUESTION: Is the transcript file in the organizer's "Google Meet" folder or "Meet Recordings"
    folder? HELP-TRANSCRIPTS and ADMIN-TRANSCRIPTS differ (I).
