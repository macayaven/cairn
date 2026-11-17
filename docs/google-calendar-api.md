# Google Calendar API v3: reading one person's ended events (title, time, invitees, description)

Researched 2026-11-13. No Calendar API was called. Every page below was fetched that day with a
plain HTTPS GET (`?hl=en`, User-Agent `curl/8`), saved under `calendar/raw/`, converted to text
under `calendar/txt/` and read in full unless a note says otherwise. Code samples in Java, .NET,
PHP, Ruby, Go, Node.js and Apps Script were skipped; protocol (HTTP/JSON) samples were read.
Final URLs are given after redirects, with each page's "Last updated" line. The token side
(service account with domain-wide delegation, one user's refresh token, the token endpoint's
errors, when a refresh token stops working) is in google-auth.md and is not repeated here.

Reference (base https://developers.google.com/workspace/calendar/api/v3/reference):

- **REF** = .../v3/reference (last updated 2026-06-17). Method index with HTTP paths.
- **EV** = .../events (2026-07-07). The Event resource.
- **EV-LIST** = .../events/list (2026-07-29); **EV-GET** = .../events/get (2026-05-12);
  **EV-INST** = .../events/instances (2026-07-29).
- **CL** = .../calendarList (2026-07-29); **CL-LIST** = .../calendarList/list (2026-07-29);
  **CL-GET** = .../calendarList/get (2026-05-12).
- **CAL** = .../calendars (2026-11-03); **CAL-GET** = .../calendars/get (2026-05-12).
- **FB** = .../freebusy/query (2026-05-12). Read for its scopes and request fields only.
- **DISCOVERY** = https://calendar-json.googleapis.com/$discovery/rest?version=v3, `revision`
  `20261111`. **It is not linked from REF** (no discovery link on any Calendar page fetched); the
  URL follows the pattern used for Meet. Used to check every scope, parameter, field name, type,
  format and default below; its descriptions were dumped to `calendar/txt/discovery.txt` so that
  [discovery] quotations could be checked.

Guides (base https://developers.google.com/workspace/calendar/api):

- **AUTH** = .../auth (2026-09-03). Calendar scope table.
- **G-OVERVIEW** = .../guides/overview (2026-09-11).
- **G-CONCEPTS** = .../concepts/events-calendars (2026-09-03).
- **G-SHARING** = .../concepts/sharing (2026-09-03).
- **G-DOMAIN** = .../concepts/domain (2026-09-03).
- **G-INVITE** = .../concepts/inviting-attendees-to-events (2026-09-03).
- **G-SYNC** = .../guides/sync (2026-11-07).
- **G-PAGING** = .../guides/pagination (2026-11-07).
- **G-RECUR** = .../guides/recurringevents (2026-09-17).
- **G-ERRORS** = .../guides/errors (2026-11-11). Read in full, from the HTML as well as the text:
  the 404 body is not in a code block on the page (it sits in a `<p>` inside a list item), and
  several bodies have trailing commas (see H).
- **G-QUOTA** = .../guides/quota (2026-11-11).
- **G-TYPES** = .../guides/event-types (2026-09-11).
- **G-STATUS** = .../guides/calendar-status (2026-09-11).
- **G-CREATE** = .../guides/create-events (2026-09-11). Read for `conferenceData` and attendees.
- **G-EXTPROP** = .../guides/extended-properties (2026-09-11).
- **G-ETAG** = .../guides/version-resources (2026-11-07).
- **G-PERF** = .../guides/performance (2026-09-11). Read for `fields` and gzip.
- **QUICKSTART** = .../quickstart/python (2026-10-12). Read for "enable the API" and its scope.
- **TROUBLESHOOT** = .../troubleshoot-authentication-authorization (2026-09-03).
- **RELEASES** = https://developers.google.com/workspace/calendar/release-notes (asked for as
  .../calendar/api/releases; the page says "Last updated 2026-09-03" yet carries an entry dated
  November 03, 2026). Read in full.
- Skimmed, nothing used: .../guides/push (2026-09-11; read to "Receive notifications"),
  .../guides/labels (2026-09-11; first section only), .../support (2026-11-02).

Other pages:

- **SCOPES-LIST** = https://developers.google.com/identity/protocols/oauth2/scopes (2026-09-14).
  Read: the page summary and the "Calendar API, v3" section only.
- **WS-CONSENT** = https://developers.google.com/workspace/guides/configure-oauth-consent
  (2026-09-03). **WS-AUTHOV** = https://developers.google.com/workspace/guides/auth-overview
  (2026-09-03).
- **CLOUD-VERIFY** = https://support.google.com/cloud/answer/13464321 ("Verification
  requirements"; no date shown). **CLOUD-RESTRICTED** = .../cloud/answer/13464325 ("Restricted
  Scopes"). **CLOUD-UNVERIFIED** = .../cloud/answer/7454865 ("Unverified apps").
  **CLOUD-FAQ** = .../cloud/answer/13463073 (asked for as .../answer/9110914; "OAuth App
  Verification Help Center"). **CLOUD-NOTNEEDED** = .../cloud/answer/13464323 ("When is
  verification not needed"). Also read, nothing about Calendar found: .../cloud/answer/13463817
  ("Frequently Asked Questions") and .../cloud/answer/13465431 ("Security Assessment"). None
  shows a date. Fetched for their titles only, not saved or read: .../cloud/answer/13461325
  (titled Submitting your app for verification), .../13463816 (Annual Recertification),
  .../13464018 (Changes to approved app).
- **TOOLS-SAFETY** = https://developers.google.com/workspace/tools-safety (2026-09-03).
- **ADMIN-LIMITS** = https://knowledge.workspace.google.com/admin/calendar/avoid-calendar-use-limits
  (2026-11-10; also reached from https://support.google.com/a/answer/2905486, which redirects here).
- **WS-POLICY** = https://developers.google.com/workspace/workspace-api-user-data-developer-policy
  (2026-09-03). **UD-POLICY** = https://developers.google.com/terms/api-services-user-data-policy
  ("Last updated February 15, 2024").

Not fetched or not found: `.../calendar/api/guides` (an index URL, returns 404); the batch guide,
the Calendar MCP guide and reference, the Cloud retry-strategy page linked from G-ERRORS, the
blog post linked from G-QUOTA, and the other quickstarts were not fetched.

Tags: **[doc: X]** = stated on page X; **[example: X]** = visible only in an example on page X;
**[discovery]** = from the discovery document; **[not stated]** = nowhere in the pages above.
**QUESTION:** marks an open point that matters for the design; **CONFLICT** quotes two Google
statements that disagree.

---

## Headline points (read first)

1. **There is no "ended since" query.** `timeMin` bounds an event's *end* and `timeMax` bounds
   its *start*: "Lower bound (exclusive) for an event's end time to filter by." and "Upper bound
   (exclusive) for an event's start time to filter by." [doc: EV-LIST; same in DISCOVERY]. A
   window `timeMin=<switched on>`, `timeMax=<now>` therefore also returns events still in
   progress; "has ended" must be worked out from `end` and the clock. No parameter filters on
   end <= T **[not stated]**.
2. **Sync tokens report changes, not endings, and exclude time bounds.** `syncToken` cannot be
   combined with `timeMin`, `timeMax`, `updatedMin`, `orderBy`, `q`, `iCalUID` or the extended
   property filters [doc: EV-LIST]. An event created weeks ago that ends now has not "changed",
   so a sync token alone does not surface it **[not stated]** (my reading of the definition). Expiry: "410 GONE"; the body has `reason` `fullSyncRequired` [doc: EV-LIST,
   G-ERRORS]. How long a token lives is **[not stated]**.
3. **No Calendar scope's class (non-sensitive / sensitive / restricted) is stated on any page
   read.** AUTH and SCOPES-LIST give scope and meaning only; CLOUD-FAQ says (paraphrased) the
   Google Cloud Console shows the categories by itself. No Calendar scope is on the restricted
   list [doc: CLOUD-RESTRICTED, WS-POLICY]. QUESTION below.
4. **The Meet meeting code is `conferenceData.conferenceId`** for `hangoutsMeet`: "ID is the
   10-letter meeting code, for example `aaa-bbbb-ccc`." [doc: EV]. Whether
   `entryPoints[].meetingCode` is also filled for Meet is **[not stated]**; Google warns that
   Meet codes get reused across events (D).
5. **Instance ids of recurring events have no documented format**, and a cancelled instance may
   carry only `id`, `recurringEventId` and `originalStartTime` [doc: EV]. If cairn keys notes by
   event id, whether an instance's `id` is stable across edits is **[not stated]** (F).
6. **Invitee names are optional.** `attendees[].displayName`: "The attendee's name, if
   available." [doc: EV]. With `maxAttendees` set and more attendees than that, "only the
   participant is returned" [doc: EV-LIST]; who "the participant" is (the calendar's own entry?) is **[not stated]**. Do not set it.
7. **Quota under domain-wide delegation lands on the service account**, not on the impersonated
   user, unless `quotaUser` is sent with an IP-restricted API key [doc: G-QUOTA]. 600 requests
   per minute per user per project; 10,000 per minute per project [doc: G-QUOTA].
8. **Rate limiting comes as 403 or 429 with the same `reason`**, `rateLimitExceeded`, and Google
   says to treat them alike [doc: G-ERRORS]. For a calendar the caller cannot access the page gives **404**
   `notFound` and lists no 403 for that case [doc: G-ERRORS].

---

## A. Service, version, base URL, scopes

- Base: `https://www.googleapis.com/calendar/v3`; "URIs relative to
  https://www.googleapis.com/calendar/v3, unless otherwise noted" [doc: REF]. DISCOVERY:
  `rootUrl` `https://www.googleapis.com/`, `servicePath` `calendar/v3/`, `batchPath`
  `batch/calendar/v3`, `id` `calendar:v3`.
- Global query parameters [discovery]: `fields`, `prettyPrint`, `alt`, `quotaUser`, `key`,
  `oauth_token`, `userIp`. `fields` gives a partial response; an invalid selection returns "an
  HTTP `400 Bad Request` status code" [doc: G-PERF].
- gzip: set `Accept-Encoding` and "modify your user agent to contain the string `gzip`"
  [doc: G-PERF].
- Before anything works: "In the Google Cloud console, enable the Google Calendar API."
  [doc: QUICKSTART]. QUICKSTART's own sample asks for `calendar.readonly`.

**Every read scope, with what the provider says it means and which read methods accept it.**
Meaning: [doc: AUTH; same words in SCOPES-LIST and DISCOVERY, up to "your Calendar" vs "your
Google Calendar"]. Methods: [doc: EV-LIST, EV-GET, EV-INST, CL-LIST, CL-GET, CAL-GET, FB; checked
against DISCOVERY, which agrees].

| Scope (`https://www.googleapis.com/auth/` + ...) | Meaning (quoted) | events.list / get / instances | calendarList.list | calendarList.get | calendars.get | freebusy.query |
|---|---|---|---|---|---|---|
| `calendar.readonly` | "See and download any calendar you can access using your Calendar." | yes | yes | yes | yes | yes |
| `calendar.events.readonly` | "View events on all your calendars." | yes | no | no | no | no |
| `calendar.events.owned.readonly` | "See the events on Google calendars you own." | yes | no | no | no | no |
| `calendar.events.public.readonly` | "See the events on public calendars." | yes | no | no | no | no |
| `calendar.events.freebusy` | "See the availability on Google calendars you have access to." | yes | no | no | no | yes |
| `calendar.calendarlist.readonly` | "See the list of Google calendars you're subscribed to." | no | yes | yes | no | no |
| `calendar.calendars.readonly` | "See the title, description, default time zone, and other properties of Google calendars you have access to." | no | no | no | yes | no |
| `calendar.freebusy` | "View your availability in your calendars." | no | no | no | no | yes |
| `calendar.settings.readonly` | "View your Calendar settings." | (settings only) | | | | |
| `calendar.acls.readonly` | "See the sharing permissions of Google calendars you own." | (ACLs only) | | | | |

Write scopes also accepted by the read methods (not wanted here): `calendar`, `calendar.events`,
`calendar.events.owned`, `calendar.app.created`, `calendar.calendarlist`, `calendar.calendars`.
AUTH also lists `calendar.addons.execute`, `calendar.addons.current.event.read` and
`calendar.addons.current.event.write` (add-ons; not in DISCOVERY); DISCOVERY also lists
`calendar.settings` (not in AUTH's table).

**Class of each scope: [not stated] for every Calendar scope.**

- AUTH's table has two columns, "Scope" and "Meaning" (read from the HTML; no icons, no hidden
  column). SCOPES-LIST's Calendar table has "Scope" and "Description" (read from the HTML).
- SCOPES-LIST: "Sensitive scopes, indicated in the Google Cloud Console, require review by
  Google." CLOUD-FAQ (paraphrased): once scopes are added to a project, the Google Cloud
  Console shows each one's category (non-sensitive, sensitive or restricted) by itself.
- CLOUD-RESTRICTED lists Gmail, Drive, Fit, Chat, Data Portability, Photos Ambient and Google
  Health scopes; **no Calendar scope**. WS-POLICY's "Restricted scopes" section names Gmail,
  Drive, Chat and Meet scopes only.
- TROUBLESHOOT, on the "This app isn't verified" warning: "your app is requesting scopes that
  provide access to sensitive user data". It does not say which Calendar scopes trigger it.
- QUESTION: is `calendar.events.readonly` (or `.owned.readonly`) sensitive? It decides whether
  the Limited Use rules (K) apply "to data derived from both Sensitive and Restricted scopes"
  [doc: WS-POLICY]. Only the Cloud console shows it.

**Narrowest scope that lists a person's events with description and attendees.** By the
method tables above, any of the event scopes works for `events.list`. What each covers, beyond
its one-line meaning, is **[not stated]**:

- `calendar.events.owned.readonly` ("calendars you own"): whether "own" means the `owner`
  access role or the calendar's data owner is **[not stated]**. G-SHARING: "By default, each user
  has owner access to their primary calendar"; CL/CAL: `dataOwner` is "Set only for secondary
  calendars." So the primary calendar is very likely covered, and a calendar the person can only
  read (an operator-named team calendar) is not. QUESTION below.
- `calendar.events.readonly` ("all your calendars"): covers calendars the person can read.
- `calendar.events.freebusy`: accepted by `events.list`, but what `events.list` returns under it
  (whether `summary`, `description`, `attendees` are present) is **[not stated]**. Not usable
  until known.
- Finding a calendar by its name needs `calendarList.list`, so `calendar.calendarlist.readonly`
  in addition (C). `events.list` itself returns the calendar's `summary`, `timeZone` and the
  caller's `accessRole` [doc: EV-LIST], so `calendars.get` is not needed for those.

**Verification and its exemptions (quoted from developers.google.com pages; the Help Center
pages CLOUD-NOTNEEDED and CLOUD-VERIFY paraphrased in our words):**

- AUTH: "If your public application uses scopes that permit access to certain user data, it must
  complete a verification process."
- WS-CONSENT: "For apps used only internally by your Google Workspace organization, scopes
  aren't listed on the consent screen and use of restricted or sensitive scopes doesn't require
  further review by Google." Its table: sensitive scopes need basic and additional app
  verification; restricted scopes also a "Security assessment".
- CLOUD-NOTNEEDED, "Internal Use apps" (paraphrased): only people in the project's Google
  Workspace or Cloud Identity organization use the app, the organization owns the project, and
  the OAuth Consent Screen is set for internal use. Also exempt, "Service-owned Data Only"
  (paraphrased): the app reads only its own data, through a Service Account, and no user data
  (data linked to a Google Account).
- CLOUD-VERIFY (paraphrased): an app that asks for restricted scopes must also show that it
  handles data securely, by passing a security assessment every year.
- UD-POLICY: "Note: If your app is only used by users within your own domain, then these
  requirements do not apply." (said of "Additional Requirements for Specific API Scopes").
- A service account with domain-wide delegation reading users' calendars: no page fetched says
  whether it needs verification **[not stated]**; it is not "Service-owned Data Only", since it
  reads user data.

**Domain-wide delegation for Calendar.** The admin steps and errors are in google-auth.md A.2 and
A.6. What is Calendar-specific:

- The admin enters, under the service account's client ID, the exact scopes the server will ask
  for; for this connector: `https://www.googleapis.com/auth/calendar.events.readonly` (or
  `.owned.readonly`) and, to find calendars by name, `.../calendar.calendarlist.readonly`.
  (google-auth.md A.6: a requested scope not listed gives `access_denied`.)
- G-DOMAIN: "An app can access domain-owned calendars without requiring user credentials if it
  authenticates using a service account. The service account must have the necessary access
  using domain-wide authority delegation."
- WS-AUTHOV: "By themselves, service accounts cannot be used to access user data customarily
  accessed using Google Workspace APIs. However, a service account can access user data by
  implementing domain-wide delegation of authority."
- EV, `attendees[]`: "Service accounts need to use domain-wide delegation of authority to
  populate the attendee list." RELEASES (March 16, 2020): "Service accounts created on or after
  March 2, 2020 are only able to invite guests using domain-wide delegation of authority."
- `primary`: "If you want to access the primary calendar of the currently logged in user, use
  the "`primary`" keyword." [doc: EV-LIST] That under delegation this is the impersonated
  user's calendar is my reading **[not stated]**.
- **Without delegation**: a calendar shared directly with the service account's address is the
  only way it could see anything, and what it then sees is **[not stated]**. Sharing does not add
  the calendar to a list: "Sharing a calendar with a user no longer automatically inserts the
  calendar into their `CalendarList`." [doc: G-SHARING]. A service account that creates a
  calendar becomes its data owner and "data ownership cannot be transferred" [doc: CAL, G-CONCEPTS].

## B. `events.list`

`GET https://www.googleapis.com/calendar/v3/calendars/calendarId/events`; "Do not supply a request
body with this method." [doc: EV-LIST]. Page note: "Authorization optional." (what an unauthorised call can
read is **[not stated]**). Response `$ref` `Events` [discovery].

- `calendarId` (path, string, required): "Calendar identifier. To retrieve calendar IDs call the
  calendarList.list method. If you want to access the primary calendar of the currently logged
  in user, use the "`primary`" keyword." [doc: EV-LIST] A calendar "is identified by an ID, which
  is an email address" and the primary calendar's ID "usually matches the user's primary email
  address" [doc: G-CONCEPTS]. Must be URL-encoded in the path (it contains `@`) **[not stated]**.

**Query parameters** [doc: EV-LIST; types, formats, defaults from DISCOVERY]:

| Parameter | Type | What the page says |
|---|---|---|
| `timeMin` | string, `date-time` | "Lower bound (exclusive) for an event's end time to filter by. Optional. The default is not to filter by end time. Must be an RFC3339 timestamp with mandatory time zone offset, for example, 2011-06-03T10:00:00-07:00, 2011-06-03T10:00:00Z. Milliseconds may be provided but are ignored. If `timeMax` is set, `timeMin` must be smaller than `timeMax`." |
| `timeMax` | string, `date-time` | "Upper bound (exclusive) for an event's start time to filter by. Optional. The default is not to filter by start time." Same format sentence; "If `timeMin` is set, `timeMax` must be greater than `timeMin`." |
| `singleEvents` | boolean | "Whether to expand recurring events into instances and only return single one-off events and instances of recurring events, but not the underlying recurring events themselves. Optional. The default is False." |
| `orderBy` | enum `startTime`, `updated` | "The default is an unspecified, stable order." `startTime`: "Order by the start date/time (ascending). This is only available when querying single events (i.e. the parameter `singleEvents` is True)". `updated`: "Order by last modification time (ascending)." |
| `showDeleted` | boolean | "Whether to include deleted events (with `status` equals "`cancelled`") in the result. Cancelled instances of recurring events (but not the underlying recurring event) will still be included if `showDeleted` and `singleEvents` are both False. If `showDeleted` and `singleEvents` are both True, only single instances of deleted events (but not the underlying recurring events) are returned. Optional. The default is False." |
| `showHiddenInvitations` | boolean | "Whether to include hidden invitations in the result. Optional. The default is False." What a hidden invitation is: **[not stated]** on any page (see G-INVITE in E). |
| `updatedMin` | string, `date-time` | "Lower bound for an event's last modification time (as a RFC3339 timestamp) to filter by. When specified, entries deleted since this time will always be included regardless of `showDeleted`." Inclusive or exclusive: **[not stated]**. Too old gives 410 `updatedMinTooLongAgo` (H); how old is too old is **[not stated]**. |
| `eventTypes` | string, repeated, enum | "This parameter can be repeated multiple times to return events of different types. If unset, returns all event types." Values: `birthday`, `default`, `focusTime`, `fromGmail`, `outOfOffice`, `workingLocation`. |
| `maxAttendees` | integer, int32, minimum 1 | "The maximum number of attendees to include in the response. If there are more than the specified number of attendees, only the participant is returned." |
| `maxResults` | integer, int32, minimum 1, default 250 | "The number of events in the resulting page may be less than this value, or none at all, even if there are more events matching the query. Incomplete pages can be detected by a non-empty `nextPageToken` field in the response. By default the value is 250 events. The page size can never be larger than 2500 events." |
| `pageToken` | string | "Token specifying which result page to return." |
| `q` | string | Free text over `summary`, `description`, `location`, attendee's `displayName` and `email`, organizer's `displayName` and `email`, and working-location labels and ids. |
| `timeZone` | string | "Time zone used in the response. Optional. The default is the time zone of the calendar." |
| `syncToken` | string | See below. |
| `iCalUID` | string | "Use this if you want to search for an event by its iCalendar ID." |
| `privateExtendedProperty`, `sharedExtendedProperty` | string, repeated | `propertyName=value` constraints. |
| `alwaysIncludeEmail` | boolean | "Deprecated and ignored." |

- **CONFLICT on repeated extended-property constraints.** EV-LIST: "This parameter might be
  repeated multiple times to return events that match all given constraints." G-EXTPROP: "You can
  repeat these fields multiple times and the constraints are OR'ed together". Not needed here.
- A `timeMax` not after `timeMin` gives 400 `timeRangeEmpty` (H) [example: G-ERRORS].
- **`syncToken`** [doc: EV-LIST]: "Token obtained from the `nextSyncToken` field returned on the
  last page of results from the previous list request. It makes the result of this list request
  contain only entries that have changed since then. All events deleted since the previous list
  request will always be in the result set and it is not allowed to set `showDeleted` to False."
  Not combinable with: `iCalUID`, `orderBy`, `privateExtendedProperty`, `q`,
  `sharedExtendedProperty`, `timeMin`, `timeMax`, `updatedMin`. "All other query parameters
  should be the same as for the initial synchronization to avoid undefined behavior. If the
  `syncToken` expires, the server will respond with a 410 GONE response code and the client
  should clear its storage and perform a full synchronization without any `syncToken`."
- The page lists the forbidden parameters as those that "cannot be specified together with
  `nextSyncToken`"; the request parameter is `syncToken`. Wording slip, same meaning.

**Response** [doc: EV-LIST; DISCOVERY schema `Events`]:

```
{
  "kind": "calendar#events",
  "etag": etag,
  "summary": string,          // the calendar's title, read-only
  "description": string,      // the calendar's description, read-only
  "updated": datetime,        // calendar's last modification time, RFC3339
  "timeZone": string,         // the calendar's time zone, read-only
  "accessRole": string,       // the caller's role on this calendar
  "defaultReminders": [ { "method": string, "minutes": integer } ],
  "nextPageToken": string,
  "nextSyncToken": string,
  "items": [ Event ]
}
```

- `nextPageToken`: "Token used to access the next page of this result. Omitted if no further
  results are available, in which case `nextSyncToken` is provided."
- `nextSyncToken`: "Omitted if further results are available, in which case `nextPageToken` is
  provided." So it comes on the last page only; G-SYNC: "If the result set is too large and the
  response gets paginated, then the `nextSyncToken` field is present only on the very last page."
- `accessRole` values: `none`, `freeBusyReader`, `reader`, `writerWithoutPrivateAccess`,
  `writer`, `owner` (meanings in E, "Private events").
- Whether `items` is absent or `[]` on an empty page is **[not stated]** (G-SYNC's Java sample
  calls `items.size()` on it [example: G-SYNC]).
- **Order.** Without `orderBy`: "unspecified, stable order". With `orderBy=startTime` (needs
  `singleEvents=true`) ascending by start; how ties are broken is **[not stated]**.
- **Paging.** "In order to retrieve the next page, perform the exact same request as previously
  and append a `pageToken` field with the value of `nextPageToken` from the previous page."
  [doc: G-PAGING]. G-PAGING also: "for some collections (such as Events) there is a hard limit on
  the number of retrieved entries that the server will never exceed. If the total number of
  events exceeds this maximum, the server returns one page of results." The limit's value is
  **[not stated]**. Whether a page token expires is **[not stated]**.
- **Time zone of returned times.** "Results of the `get()`, `list()`, and `instances()` methods
  are returned in the time zone that you specify in the `timeZone` parameter. If you omit this
  parameter, then these methods all use the calendar time zone as the default." [doc: G-CONCEPTS]
- **Free/busy readers.** "When a user who has free/busy permissions queries `events.list()`, it
  behaves as if `singleEvent` is `true`." [doc: G-RECUR]

## C. `calendarList.list`, `calendarList.get`, `calendars.get`

**`calendarList.list`**: `GET https://www.googleapis.com/calendar/v3/users/me/calendarList`
[doc: CL-LIST]. "Note: Requires authorization." Scopes: `calendar.readonly`, `calendar`,
`calendar.calendarlist`, `calendar.calendarlist.readonly`.

| Parameter | What the page says |
|---|---|
| `maxResults` | "By default the value is 100 entries. The page size can never be larger than 250 entries." |
| `minAccessRole` | "The minimum access role for the user in the returned entries. Optional. The default is no restriction." Values `freeBusyReader`, `owner`, `reader`, `writer`, `writerWithoutPrivateAccess`. |
| `pageToken` | "Token specifying which result page to return." |
| `showDeleted` | "Whether to include deleted calendar list entries in the result. Optional. The default is False." |
| `showHidden` | "Whether to show hidden entries. Optional. The default is False." |
| `showOwnOrganizationOnly` | "Whether to show only entries for calendars from the organization. This parameter is only applicable to Google Workspace users." (new July 14, 2026 [doc: RELEASES]) |
| `syncToken` | Same mechanism as events; "If only read-only fields such as calendar properties or ACLs have changed, the entry won't be returned." Cannot be combined with `minAccessRole` or `showOwnOrganizationOnly`; expiry is "410 GONE". |

Response: `{"kind": "calendar#calendarList", "etag", "nextPageToken", "nextSyncToken", "items":
[CalendarListEntry]}`, with the same next-page/next-sync rule as events [doc: CL-LIST].

**CalendarListEntry** [doc: CL; types from DISCOVERY]:

| Field | Type | Description (quoted or summarised) |
|---|---|---|
| `id` | string | "Identifier of the calendar." |
| `summary` | string | "Title of the calendar. Read-only." |
| `summaryOverride` | string | "The summary that the authenticated user has set for this calendar. Optional." |
| `description`, `location` | string | read-only, optional |
| `timeZone` | string | "The time zone of the calendar. Optional. Read-only." |
| `dataOwner` | string | "The email of the owner of the calendar. Set only for secondary calendars. Read-only." |
| `accessRole` | string | "The effective access role that the authenticated user has on the calendar." Values `freeBusyReader`, `reader`, `writerWithoutPrivateAccess`, `writer`, `owner`. |
| `primary` | boolean, default false | "Whether the calendar is the primary calendar of the authenticated user. Read-only. Optional. The default is False." |
| `hidden` | boolean, default false | "Whether the calendar has been hidden from the list. Optional. The attribute is only returned when the calendar is hidden, in which case the value is `true`." |
| `deleted` | boolean, default false | "Whether this calendar list entry has been deleted from the calendar list. Read-only. Optional. The default is False." |
| `selected` | boolean | "Whether the calendar content shows up in the calendar UI." |
| `conferenceProperties.allowedConferenceSolutionTypes[]` | list of string | `eventHangout`, `eventNamedHangout`, `hangoutsMeet` |
| `autoAcceptInvitations` | boolean | "Only valid for resource calendars. Read-only." |
| also | | `kind` (`calendar#calendarListEntry`), `etag`, `colorId`, `backgroundColor`, `foregroundColor`, `defaultReminders[]`, `notificationSettings` |

**Finding a calendar by its name.** No parameter filters by name **[not stated]**; list all
entries and compare `summary` (or the person's `summaryOverride`). Whether names are unique is **[not stated]**. A calendar shared with the person is not on the list until inserted
[doc: G-SHARING, quoted in A], so a name lookup can miss a calendar the person can read.
QUESTION below. An entry the person hid has `hidden: true` and is left out unless
`showHidden=true`.

**`calendarList.get`**: `GET .../users/me/calendarList/calendarId`; returns one
CalendarListEntry; scopes add `calendar.app.created` [doc: CL-GET].

**`calendars.get`**: `GET https://www.googleapis.com/calendar/v3/calendars/calendarId`; "Returns
metadata for a calendar." Scopes `calendar.readonly`, `calendar`, `calendar.app.created`,
`calendar.calendars`, `calendar.calendars.readonly` [doc: CAL-GET]. Calendar resource [doc: CAL]:
`kind` (`calendar#calendar`), `etag`, `id`, `summary`, `description`, `location`, `timeZone` ("(Formatted as an IANA Time
Zone Database name, e.g. "Europe/Zurich".) Optional."), `dataOwner`, `conferenceProperties`,
`labelProperties`, `autoAcceptInvitations`, `workingHours` ("Only available for primary
calendars of Google Workspace users."; new November 3, 2026 [doc: RELEASES]).

- Calendars vs CalendarList: "The CalendarList is a collection of all calendar entries that a
  user has added to their list (shown in the left panel of the web UI)." [doc: G-CONCEPTS]
- A primary calendar "can never be deleted or "un-owned" by the user" while the account exists
  [doc: G-CONCEPTS]. A secondary calendar's deletion as seen through `events.list` (404? 410?) is
  **[not stated]**.

## D. The Event resource

[doc: EV; types and formats checked in DISCOVERY schema `Event`]. "Read-only" and defaults as
the page gives them. `datetime` = RFC3339 string (`format: date-time`); `date` = `yyyy-mm-dd`
(`format: date`).

| Field | Type | Description (quoted or summarised) |
|---|---|---|
| `kind` | string | `calendar#event` |
| `etag` | string | "ETag of the resource." |
| `id` | string | "Opaque identifier of the event." Client-supplied ids use base32hex characters ("lowercase letters a-v and digits 0-9"), length 5 to 1024, "the ID must be unique per calendar". |
| `status` | string | `confirmed` ("This is the default status."), `tentative`, `cancelled` ("The event is cancelled (deleted)."). See E. |
| `htmlLink` | string | "An absolute link to this event in the Google Calendar Web UI. Read-only." |
| `created` | datetime | "Creation time of the event (as a RFC3339 timestamp). Read-only." |
| `updated` | datetime | "Last modification time of the main event data (as a RFC3339 timestamp). Updating event reminders will not cause this to change. Read-only." |
| `summary` | string | "Title of the event." |
| `description` | string | "Description of the event. Can contain HTML. Optional." Which HTML tags, and whether plain text is ever escaped, is **[not stated]**. |
| `location` | string | "Geographic location of the event as free-form text. Optional." |
| `colorId`, `eventLabelId` | string | display colour / label |
| `creator` | object | `id`, `email`, `displayName`, `self`; "The creator of the event. Read-only." Each "if available". |
| `organizer` | object | `id`, `email`, `displayName`, `self`. "If the organizer is also an attendee, this is indicated with a separate entry in `attendees` with the `organizer` field set to True." `self`: "Whether the organizer corresponds to the calendar on which this copy of the event appears." |
| `start` | EventDateTime | "The (inclusive) start time of the event. For a recurring event, this is the start time of the first instance." |
| `end` | EventDateTime | "The (exclusive) end time of the event. For a recurring event, this is the end time of the first instance." |
| `endTimeUnspecified` | boolean, default false | "Whether the end time is actually unspecified. An end time is still provided for compatibility reasons, even if this attribute is set to True." |
| `recurrence[]` | list of string | "List of RRULE, EXRULE, RDATE and EXDATE lines for a recurring event, as specified in RFC5545." "This field is omitted for single events or instances of recurring events." |
| `recurringEventId` | string | "For an instance of a recurring event, this is the `id` of the recurring event to which this instance belongs. Immutable." |
| `originalStartTime` | EventDateTime | "It uniquely identifies the instance within the recurring event series even if the instance was moved to a different time. Immutable." |
| `transparency` | string, default `opaque` | `opaque` blocks time ("Show me as to Busy"), `transparent` does not. |
| `visibility` | string, default `default` | `default`, `public`, `private` ("only event attendees may view event details"), `confidential` ("The event is private. This value is provided for compatibility reasons."). See E. |
| `iCalUID` | string | "Event unique identifier as defined in RFC5545." "in recurring events, all occurrences of one event have different `id`s while they all share the same `iCalUID`s." |
| `sequence` | integer, int32 | "Sequence number as per iCalendar." |
| `attendees[]` | list of EventAttendee | below |
| `attendeesOmitted` | boolean, default false | "Whether attendees may have been omitted from the event's representation. When retrieving an event, this may be due to a restriction specified by the `maxAttendee` query parameter." |
| `extendedProperties` | object | `private` and `shared`, each a map of string to string |
| `hangoutLink` | string | "An absolute link to the Google Hangout associated with this event. Read-only." |
| `conferenceData` | ConferenceData | below |
| `gadget` | object | deprecated; "only used for returning birthday calendar metadata" |
| `anyoneCanAddSelf` | boolean, default false | deprecated |
| `guestsCanInviteOthers` | boolean, default true | |
| `guestsCanModify` | boolean, default false | |
| `guestsCanSeeOtherGuests` | boolean, default true | "Whether attendees other than the organizer can see who the event's attendees are." What the attendee list looks like on a guest's copy when it is false is **[not stated]**. |
| `privateCopy` | boolean, default false | "If set to True, Event propagation is disabled." |
| `locked` | boolean, default false | locked copy; main fields cannot be changed |
| `reminders` | object | `useDefault`, `overrides[]` (the caller's own reminders) |
| `source` | object | `url`, `title`; "Can only be seen or modified by the creator of the event." |
| `workingLocationProperties`, `outOfOfficeProperties`, `focusTimeProperties`, `birthdayProperties` | objects | type-specific data (E) |
| `attachments[]` | list | `fileUrl`, `title`, `mimeType`, `iconLink`, `fileId`; "There can be at most 25 attachments per event". `fileId`: "For Google Drive files, this is the ID of the corresponding `Files` resource entry in the Drive API." |
| `eventType` | string, default `default` | E |

**EventDateTime** [doc: EV; DISCOVERY]:

| Field | Type | Description (quoted) |
|---|---|---|
| `date` | string, `date` | "The date, in the format "yyyy-mm-dd", if this is an all-day event." |
| `dateTime` | string, `date-time` | "The time, as a combined date-time value (formatted according to RFC3339). A time zone offset is required unless a time zone is explicitly specified in `timeZone`." |
| `timeZone` | string | "The time zone in which the time is specified. (Formatted as an IANA Time Zone Database name, e.g. "Europe/Zurich".) For recurring events this field is required and specifies the time zone in which the recurrence is expanded. For single events this field is optional and indicates a custom time zone for the event start/end." |

**EventAttendee** [doc: EV; DISCOVERY]:

| Field | Type | Description (quoted) |
|---|---|---|
| `email` | string | "The attendee's email address, if available." |
| `displayName` | string | "The attendee's name, if available. Optional." |
| `id` | string | "The attendee's Profile ID, if available." |
| `self` | boolean, default false | "Whether this entry represents the calendar on which this copy of the event appears. Read-only. The default is False." |
| `organizer` | boolean | "Whether the attendee is the organizer of the event. Read-only. The default is False." (no `default` in DISCOVERY) |
| `resource` | boolean, default false | "Whether the attendee is a resource." (rooms: "To book a room or other resource, add its email address as an event attendee." [doc: G-DOMAIN]) |
| `optional` | boolean, default false | "Whether this is an optional attendee. Optional. The default is False." |
| `responseStatus` | string | `needsAction` ("The attendee has not responded to the invitation"), `declined`, `tentative`, `accepted` |
| `comment` | string | "The attendee's response comment. Optional." |
| `additionalGuests` | integer, default 0 | "Number of additional guests." |
| `asyncOperation` | string | `inProgress` while e.g. a large group is being expanded; otherwise absent |

- `email` for a person with no real address: EV-GET says of the deprecated `alwaysIncludeEmail`:
  "A value will always be returned in the `email` field for the organizer, creator and attendees,
  even if no real email address is available (i.e. a generated, non-working value will be
  provided)." So `email` can be a fake address; how to recognise one is **[not stated]**.
- A group invited as one attendee: whether it appears as one entry or is expanded into members
  is **[not stated]** beyond `asyncOperation`'s "listing of members of large attendee groups".
- A limit on attendees per event is **[not stated]**; "if more than 200 guests are invited to the
  event, response status is not propagated to the guests" [doc: EV, G-INVITE].

**ConferenceData** [doc: EV; DISCOVERY schema `ConferenceData`]:

| Field | Type | Description (quoted or summarised) |
|---|---|---|
| `conferenceSolution.key.type` | string | `eventHangout` (deprecated), `eventNamedHangout` (deprecated), `hangoutsMeet` ("for Google Meet (http://meet.google.com)"), `addOn` ("for 3P conference providers"). "If a client encounters an unfamiliar or empty type, it should still be able to display the entry points." "Unset for a conference with a failed create request." |
| `conferenceSolution.name`, `.iconUri` | string | user-visible name and icon |
| `conferenceId` | string | "The ID of the conference. Can be used by developers to keep track of conferences, should not be displayed to users." For `hangoutsMeet`: "ID is the 10-letter meeting code, for example `aaa-bbbb-ccc`." For `addOn`: "ID is defined by the third-party provider." Optional. |
| `entryPoints[]` | list of EntryPoint | "All of them must belong to the same conference." |
| `entryPoints[].entryPointType` | string | `video` (zero or one), `phone` (zero or more), `sip` (zero or one), `more` (zero or one) |
| `entryPoints[].uri` | string | max 1300 characters; `video` needs `http:` or `https:` |
| `entryPoints[].label` | string | "for `video`: meet.google.com/aaa-bbbb-ccc" [example: EV] |
| `entryPoints[].meetingCode`, `.accessCode`, `.passcode`, `.password`, `.pin` | string | max 128 characters each; "populate only the subset of {`meetingCode`, `accessCode`, `passcode`, `password`, `pin`} fields that match the terminology that the conference provider uses." |
| `entryPoints[].regionCode`, `.entryPointFeatures[]` | string, list | only in DISCOVERY: region for phone access; "such as being toll or toll-free" |
| `createRequest` | object | `requestId`, `conferenceSolutionKey.type`, `status.statusCode` (`pending`, `success`, `failure`) |
| `notes` | string | "Can contain HTML. The maximum length is 2048 characters." |
| `signature` | string | server-generated |
| `parameters.addOnParameters.parameters` | map | only in DISCOVERY |

**Where the Meet meeting code is:**

- `conferenceData.conferenceId` when `conferenceSolution.key.type` is `hangoutsMeet`, in the
  form `aaa-bbbb-ccc` [doc: EV]. "10-letter" with the example's 3-4-3 shape; the Meet API gives
  the code's format as `[a-z]+-[a-z]+-[a-z]+` (google-meet-api.md F), which is looser.
- The `video` entry point's `label` and `uri` carry `meet.google.com/<code>` [example: EV, for
  `label` only]. A full Meet entry-point example is **[not stated]** on any page.
- `entryPoints[].meetingCode`: whether Calendar fills it for Meet is **[not stated]**.
- `hangoutLink`: described as a link to "the Google Hangout"; whether it is set for Meet
  conferences, and in what form, is **[not stated]**.
- Whether events carry Meet conferences added in the Calendar UI is **[not stated]** on these
  pages (the Meet notes assume so). The API stopped auto-adding Meet in 2020: "We stopped auto-populating Meet for API calls (such as
  `Events.insert`) to prevent Meet conferences being added unintentionally via 3rd parties."
  [doc: RELEASES]. A `conferenceData` with `createRequest.status.statusCode` `pending` may lack
  entry points [doc: EV, G-CREATE].
- **Reuse. CONFLICT.** RELEASES (February 17, 2026): "Reusing Google Meet codes across different
  events can cause access issues, and can expose meeting details to unintended users."; EV:
  "Reusing Google Meet conference data across different events can cause access issues and expose
  meeting details to unintended users." Yet G-CREATE: "If you wish to schedule multiple Calendar
  events with the same conference details, you can copy the entire `conferenceData` from one
  event to another." So one code can sit on several events, and every instance of a recurring
  event shares its series' conference **[not stated]** (no page says instances get their own
  code). RELEASES (July 12, 2017) lists among the then-new abilities: "Copy conference data from
  one event to another." The Meet API adds that a meeting code can later be reused for a different meeting
  space (google-meet-api.md F). QUESTION below.
- The Meet Help Center says the transcript is also linked in the Calendar event when the
  meeting is tied to an event and held at the scheduled time (google-meet-api.md F). Whether that link is an `attachments[]`
  entry, and whether its `fileId` equals the Meet transcript's `docsDestination.document`, is
  **[not stated]**. QUESTION below.

## E. Time, status, visibility and event types

**Times.**

- Timed vs all-day: "Timed events use the `start.dateTime` and `end.dateTime` fields"; "All-day
  events use the `start.date` and `end.date` fields to specify when they occur. Note that the
  timezone field has no significance for all-day events." "The start and end of the event must
  both be timed or both be all-day." [doc: G-CONCEPTS]
- All-day end is exclusive: `end` is "The (exclusive) end time of the event." [doc: EV]; a one-day
  all-day event has `"start": {"date": "2015-06-01"}`, `"end": {"date": "2015-06-02"}`
  [example: G-CONCEPTS]. G-TYPES: birthday `start` and `end` must "define an all-day event which
  spans exactly one day", with `2024-01-29` to `2024-01-30` [example: G-TYPES].
- All-day events against `timeMin`/`timeMax`: "The calendar time zone is used to calculate start
  and end times of all-day events to determine whether they fall within the filter
  specification." [doc: G-CONCEPTS] So whether an all-day event has ended is to be judged at midnight
  of `end.date` in the calendar's `timeZone` (from the list response).
- Offsets in responses: times come back in the request's `timeZone` or the calendar's (B).
  `created`/`updated` look like `"2011-05-23T22:27:01.000Z"` [example: G-RECUR]; whether they are
  always UTC with milliseconds is **[not stated]**.
- Which timestamp says what: `created` = created; `updated` = last change of "the main event data"
  (not reminders); `end` = when it ends (exclusive). No field says when an event was deleted
  **[not stated]**.
- `endTimeUnspecified: true`: an `end` is still present "for compatibility reasons"; what value
  it holds is **[not stated]**. QUESTION below.
- Recurring events must carry `timeZone`: "For recurring events, you must always specify a single
  time zone to expand the recurrences of the event." [doc: G-CONCEPTS]

**Deleted, cancelled and edited events** [doc: EV, `status`]:

- "The list method returns cancelled events only on incremental sync (when `syncToken` or
  `updatedMin` are specified) or if the `showDeleted` flag is set to `true`. The get method
  always returns them."
- Cancelled exception of a live series: "Cancelled exceptions of an uncancelled recurring event
  indicate that this instance should no longer be presented to the user." "Cancelled exceptions
  are only guaranteed to have values for the `id`, `recurringEventId` and `originalStartTime`
  fields populated. The other fields might be empty."
- Any other cancelled event is a deletion: "Clients should remove their locally synced copies.
  Such cancelled events will eventually disappear, so do not rely on them being available
  indefinitely." "Deleted events are only guaranteed to have the `id` field populated." How long
  they remain is **[not stated]**.
- "On the organizer's calendar, cancelled events continue to expose event details (summary,
  location, etc.) so that they can be restored (undeleted). Similarly, the events to which the
  user was invited and that they manually removed continue to provide details. However,
  incremental sync requests with `showDeleted` set to false will not return these details."
- Moving an event to another organizer "will leave behind a cancelled event where only the `id`
  field is guaranteed to be populated" when the original organizer is not an attendee.
- An edit changes `updated` and `etag` (G-ETAG: "Every resource has a version field that changes
  every time the resource changes — the `etag` field."); the event keeps its `id` **[not stated]**
  (implied by update methods taking `eventId`).
- Attendee copies: "Inviting someone to an event puts a copy of that event on their calendar."
  [doc: G-SHARING]. "The calendar where the event is created is the organizer calendar. This
  calendar owns the shared event information, including ID, start and end time, summary, and
  description." [doc: G-INVITE]. Attendees "can also change the shared properties of the event.
  However, these changes are only reflected on their own copy and might be lost if the organizer
  makes a change." [doc: G-INVITE] So the person's copy can differ from the organizer's.

**Invitations that are not on the calendar.** G-INVITE: with the setting `When I respond in
email`, "all invitations aren't added to their calendar until the user RSVPs"; with `Only if the
sender is known` an unknown organizer's event is added only after "Add to calendar" or an RSVP.
Whether such invitations are what `showHiddenInvitations` returns is **[not stated]**. QUESTION
below. **CONFLICT:** G-CREATE: "The event you create appears on all the primary Google Calendars
of the attendees you included with the same event ID." vs the G-INVITE rules just quoted.

**Private events.** **CONFLICT on who sees details.**

- EV, `visibility`: "`private`" - "The event is private and only event attendees may view event
  details."
- G-SHARING: "The details of this event are only visible to users with at least `writer` access
  to the calendar."
- EV-LIST / CL `accessRole`: `reader` and `writerWithoutPrivateAccess`: "Private events will
  appear ... but event details will be hidden"; `writer`: "Private events will appear to users with
  writer access, and event details will be visible."
- G-SHARING on `public`: "visible to everyone with at least `freeBusyReader` access" vs EV:
  "visible to all readers of the calendar".
- What a hidden-details private event looks like in JSON (which fields are blanked, whether
  `summary` reads "busy") is **[not stated]**. Reading a person's own primary calendar as that
  person (owner) shows details by the `writer`/`owner` rule; an operator-named calendar the
  person only reads does not. QUESTION below.
- Visibility "has no meaning for non-shared calendars" [doc: G-SHARING]. A Workspace domain
  setting can cap sharing: "even if you grant writer access to the public, users outside the
  domain will only see the free-busy details." [doc: G-SHARING]

**Event types** [doc: EV, G-TYPES, G-STATUS, RELEASES]:

| `eventType` | What it is (quoted or summarised) |
|---|---|
| `default` | "A regular event or not further specified." |
| `focusTime` | "A focus-time event." Only on primary calendars; must be timed ("Focus times cannot be all-day events.") [doc: G-STATUS]. Has `focusTimeProperties` (`autoDeclineMode`, `declineMessage`, `chatStatus`). |
| `outOfOffice` | "An out-of-office event." Timed only [doc: G-STATUS]. Since 2021 its organizer is `unknownorganizer@calendar.google.com` [doc: RELEASES, February 08, 2021]. Has `outOfOfficeProperties`. |
| `workingLocation` | "A working location event." Timed or one all-day day; `visibility` `public`, `transparency` `transparent` [doc: G-STATUS]. Several may overlap. |
| `birthday` | "A special all-day event with an annual recurrence." From Contacts or the profile; `birthdayProperties` (`type`, `contact` as `people/c12345`, `customTypeName`). |
| `fromGmail` | "An event from Gmail. This type of event cannot be created." Since May 30, 2024 its organizer is "the email address of the email recipient" [doc: RELEASES]. |

- "If unset, returns all event types." [doc: EV-LIST]; G-TYPES: "If no type is specified, all
  event types will be returned." Status events "are only available on primary calendars, and to
  some Google Calendar users" [doc: G-STATUS]. To read meetings only, pass
  `eventTypes=default` (and decide on `fromGmail`). QUESTION below.

## F. Recurring events, `events.instances` and `events.get`

- Default listing: "The `events.list()` request by default only returns single events, recurring
  events, and exceptions; instances that are not exceptions are not returned. If the
  `singleEvents` parameter is set `true` then all individual instances appear in the result, but
  underlying recurring events don't." [doc: G-RECUR]
- An instance: "Unlike their parent recurring events, instances do not have the `recurrence`
  field set." Specific fields: `recurringEventId` and `originalStartTime`, which "can be
  different from the actual `start` time if the instance was rescheduled." [doc: G-RECUR]
- Exceptions: "Instances that differ from their parent recurring event are called exceptions."
  "You can also cancel an instance altogether without removing the recurring event (instance
  cancellations are reflected in the event `status`)." [doc: G-CONCEPTS]
- **Instance ids.** Different per occurrence, shared `iCalUID` [doc: EV]. The format of an
  instance's `id`, and whether it stays the same when the instance is edited into an exception
  or moved, is **[not stated]**; G-RECUR's example uses the placeholder `instanceId`
  [example: G-RECUR]. QUESTION below.
- **With `singleEvents=true` and `showDeleted=false`**, whether a cancelled instance is left out
  is **[not stated]**; EV-LIST describes only the both-False and both-True cases (B).
- **Series edits.** "this and following" edits: G-RECUR warns against editing instances one by
  one. How a series split ("this and following") appears in the API (a new series id? the old
  series given an `UNTIL`?) is **[not stated]**.
- **Visibility on a series.** "If the new setting is more restrictive (e.g. from public to
  private), it is applied to all instances." [doc: EV, G-SHARING, RELEASES July 07, 2026]
- **CONFLICT on `originalStartTime`'s shape.** EV and DISCOVERY: an EventDateTime object
  (`date`/`dateTime`/`timeZone`). G-RECUR's example: `"originalStartTime":
  "2011-06-03T10:00:00.000-07:00"` (a bare string) [example: G-RECUR]. A fake should use the
  object form.

**`events.instances`**: `GET .../calendars/calendarId/events/eventId/instances`; "Returns
instances of the specified recurring event." [doc: EV-INST]. Parameters: `maxAttendees`,
`maxResults` ("By default the value is 250 events. The page size can never be larger than 2500
events."; DISCOVERY gives no `default` field here), `originalStart` ("The original start time of
the instance in the result."; its format is **[not stated]**), `pageToken`, `showDeleted`
("Cancelled instances of recurring events will still be included if `singleEvents` is False.",
though `instances` has no `singleEvents` parameter), `timeMin`, `timeMax`, `timeZone`. Response:
the same `Events` envelope as `events.list`.

- **CONFLICT on `timeMin`.** EV-LIST: "Lower bound (exclusive) for an event's end time to
  filter by." EV-INST: "Lower bound (inclusive) for an event's end time to filter by." Both pages
  and DISCOVERY say so. `timeMax` is "exclusive" on both. QUESTION below.
- Not needed if `events.list` is called with `singleEvents=true`.

**`events.get`**: `GET .../calendars/calendarId/events/eventId`; parameters `maxAttendees`,
`timeZone`, `alwaysIncludeEmail` (ignored); returns one Event [doc: EV-GET]. It returns cancelled
events too ("The get method always returns them." [doc: EV]). Fetching by iCalendar id goes
through `events.list?iCalUID=` [doc: EV-GET].

## G. Incremental reading

[doc: G-SYNC unless noted]

- Two stages: "Initial full sync is performed once at the very beginning"; "Incremental sync is
  performed repeatedly and updates the client with all the changes that happened ever since the
  previous sync." Example token: `"nextSyncToken": "CPDAlvWDx70CEPDAlvWDx70CGAU=",`
  [example: G-SYNC].
- During a paged full sync: "You don't need to worry about any new entries appearing while you
  are paginating — they won't be missed. The information needed for the server to generate a
  correct sync token is encoded in the page token."
- Incremental results "will always contain deleted entries". A large change set comes back paged:
  repeat "the exact same list query ... (with the exact same `syncToken`), append the
  `pageToken`" until the last page brings a new sync token.
- Disallowed parameters on an incremental request: "The response code for list queries
  containing disallowed restrictions is `400`." Body **[not stated]**.
- Expiry: "Sometimes sync tokens are invalidated by the server, for various reasons including
  token expiration or changes in related ACLs. In such cases, the server will respond to an
  incremental request with a response code `410`. This should trigger a full wipe of the client's
  store and a new full sync." Token lifetime: **[not stated]**. The 410 body: H.
- **CONFLICT on restricting the full sync.** G-SYNC: "You can optionally restrict the list request
  using request parameters if you only want to synchronize a specific subset of resources." and
  its Java sample sets `timeMin` (one year back) on the full sync only, then sends just
  `syncToken`, with a code comment saying sync tokens are not compatible with most filters but a full
  sync may be limited to a date range [example: G-SYNC]. But G-SYNC also: "Each list
  request should use the same set of query parameters, including the initial request." and EV-LIST
  forbids `timeMin` with a sync token and says "All other query parameters should be the same as
  for the initial synchronization to avoid undefined behavior." Whether a token from a
  `timeMin`-bounded full sync then reports changes outside that window is **[not stated]**.
  QUESTION below.
- **Legacy sync. CONFLICT on the name.** G-SYNC: "using the `modifiedSince` field to retrieve
  updated events. This approach is no longer recommended". No method page has `modifiedSince`;
  EV-LIST has `updatedMin` with the same role.
- **An event that has ended cannot be asked for.** Neither `syncToken` nor any filter selects
  on end time being in the past (B, headline 1). The usable request is
  `singleEvents=true&timeMin=<watermark>&timeMax=<now>` (both RFC3339 with offset), keeping only
  items whose `end` (exclusive) is at or before now; an event that starts before `now` and ends
  after it is returned and must be held back. With `timeMin` exclusive on `end`, an event ending
  exactly at the watermark is not returned [doc: EV-LIST].
- Push notifications (`events.watch`) exist and are recommended over polling [doc: G-QUOTA]; they need
  an HTTPS callback URL with a valid certificate [doc: G-PUSH]. Not needed for a timer-driven source.

## H. Errors

Source for this section: [doc: G-ERRORS]. All bodies below use the older `error.errors[]` shape with `domain`, `reason`,
`message`, sometimes `locationType` and `location`, plus `error.code` and `error.message`.
Whether Calendar also sends `error.status` or `error.details` is **[not stated]** (see
google-auth.md C.2 for the general shapes). **Several examples on the page are not valid JSON**:
trailing commas after the last member in the 400, 401, 410 (`fullSyncRequired`,
`updatedMinTooLongAgo`), 412 and 500 bodies; the fake should emit valid JSON.

| Status | `domain` / `reason` | `message` (as shown) | Meaning and suggested action (quoted or summarised) |
|---|---|---|---|
| 400 | `calendar` / `timeRangeEmpty`, `locationType` `parameter`, `location` `timeMax` | "The specified time range is empty." | "Because this is a permanent error, do not retry." |
| 401 | `global` / `authError`, `locationType` `header`, `location` `Authorization` | "Invalid Credentials" | "Get a new access token using the long-lived refresh token."; for a service account, re-check its setup |
| 403 | `usageLimits` / `userRateLimitExceeded` | "User Rate Limit Exceeded" | "One of the limits from the Developer Console has been reached."; back off |
| 403 | `usageLimits` / `rateLimitExceeded` | "Rate Limit Exceeded" | "The user has reached Google Calendar API's maximum request rate per calendar or per authenticated user." |
| 403 | `usageLimits` / `quotaExceeded` | "Calendar usage limits exceeded." | "The user reached one of the Google Calendar limits in place to protect Google users and infrastructure from abusive behavior." |
| 403 | `calendar` / `forbiddenForNonOrganizer` | "Shared properties can only be changed by the organizer of the event." | writes only |
| 404 | `global` / `notFound` | "Not Found" | "when the requested resource (with the provided ID) has never existed" or "when accessing a calendar that the user can not access". Suggested action: "Use exponential backoff." |
| 409 | `global` / `duplicate` | "The requested identifier already exists." | writes only |
| 409 | `global` / `conflict` | "Conflict" | batched writes only |
| 410 | `calendar` / `fullSyncRequired`, `location` `syncToken` | "Sync token is no longer valid, a full sync is required." | "wipe the store and re-sync" |
| 410 | `calendar` / `updatedMinTooLongAgo`, `location` `updatedMin` | "The requested minimum modification time lies too far in the past." | same |
| 410 | `global` / `deleted` | "Resource has been deleted" | deleting an already-deleted event |
| 412 | `global` / `conditionNotMet`, `location` `If-Match` | "Precondition Failed" | etag mismatch on a write |
| 429 | `usageLimits` / `rateLimitExceeded` | "Rate Limit Exceeded" | "the user has sent too many requests in a given amount of time" |
| 500 | `global` / `backendError` | "Backend Error" | "Use exponential backoff." |

- **403 vs 429**: "`rateLimitExceeded` errors can return either 403 or 429 error codes—currently
  they are functionally similar and should be responded to in the same way, by using exponential
  backoff." G-QUOTA: "you're rate limited and receive a `403 usageLimits` status code or a `429
  usageLimits` status code". So the connector must read `error.errors[0].reason` on a 403 to
  tell a rate limit from a permission problem.
- **Not on the page, needed by the connector [not stated]**: a 403 for a token whose scopes do
  not cover the method (reason, body); a 403 for a calendar the caller knows but may not read
  (the page says 404 for that); 503; the 400 body for a forbidden parameter with `syncToken`.
- `Retry-After` on 403/429: **[not stated]**.
- Conditional GET: `If-None-Match` with an etag gives "a 304 (Not Modified) response code"
  when unchanged [doc: G-ETAG].

## I. Quotas and limits

[doc: G-QUOTA unless noted]

- "As of May 1, 2026, the usage limits for this API were updated. Google Cloud projects that made
  any use of this API between November 2025 and April 2026 will continue with their previously
  set usage quotas. Cloud projects created on or after May 1, 2026 are subject to the new API
  quotas."

| Limit | Value |
|---|---|
| Per minute per project | 10,000 requests |
| Per minute per user per project | 600 requests |
| Daily billing threshold, per day per project | 1,000,000 requests ("before charges apply"; "You cannot request an increase on this daily threshold limit.") |

- Windows: "Quotas are calculated per minute using a sliding window."
- Units: "each patch request consumes three quota units" [doc: EV, CL, CAL]. The cost of a read
  call (one request per page?) is **[not stated]**.
- Charges: "Full billing details will be shared later in 2026 with at least 90 days' notice
  before any changes take effect." and "Exceeding the quota request limits is planned to incur
  charges to your Google Cloud billing account later in 2026." TOOLS-SAFETY: "API usage over
  standard daily thresholds will generate charges on your Google Cloud bill." (after 90 days'
  notice). Whether charging has begun by 2026-11-13 is **[not stated]**.
- **Service accounts.** "API calls by a service account are considered to be using a single
  account." and "If your application is performing requests using domain-wide delegation, by
  default the service account is charged with regard to "per minute per user per project"
  quotas, and not the user you're impersonating." `quotaUser` (or `x-goog-quota-user`, "an
  arbitrary string (up to 40 characters)") moves the charge only if the request carries "an
  IP-restricted API key" in `key` and no `X-Goog-User-Project` header; otherwise "the Calendar
  API ignores `quotaUser` and charges quota to the authenticated account". **CONFLICT
  (incomplete advice):** G-ERRORS says only "consider using a service account with domain-wide
  delegation and setting the `quotaUser` parameter", without the API key condition.
- Backoff: "If the request fails, wait 1 + `random_number_milliseconds` and retry the request.",
  then 2, 4, ...; wait `min(((2^n)+random_number_milliseconds), maximum_backoff)`, with
  `random_number_milliseconds` "less than or equal to 1,000" and `maximum_backoff` "typically 32
  or 64 seconds"; "At some point, clients should be prevented from retrying indefinitely."
- Traffic shape: "a common bad practice for a Calendar client is to perform a full sync at
  midnight"; "If you need to perform an operation on a regular basis, vary the interval +/- 25%."
- Polling: "An anti-pattern here is to repeatedly poll every calendar of interest. This will very
  quickly use up all your quota." (push recommended).
- Calendar usage limits (ADMIN-LIMITS) concern invitations, event creation, calendars created and
  shares (e.g. users creating more than 100,000 events in a short period); none concerns
  reading; read limits there are **[not stated]**.
- Other sizes: extended properties up to 300 per event, keys 44 and values 1024 characters
  [doc: G-EXTPROP]; at most 25 attachments [doc: EV]; `conferenceData.notes` 2048 characters
  [doc: EV]. Maximum `description` length **[not stated]**.

## J. On the customer's side

- **Enable the API** in the Cloud project that owns the service account or OAuth client
  [doc: QUICKSTART].
- **Service account route:** a super admin grants domain-wide delegation to the service account's
  client ID with the exact Calendar scopes (A; google-auth.md A.2). Changes can take up to 24
  hours to apply (google-auth.md A.2).
- **Refresh token route:** the person consents to the scopes; for an Internal app no verification
  is needed (A). Refresh-token lifetime rules: google-auth.md B.4.
- **Operator-named calendars:** the person must have at least `reader` on them; who sees private
  event details is a CONFLICT (E); to find one by name it must be on the person's calendar list
  (C). A domain sharing policy can cap what is visible [doc: G-SHARING].
- **What an admin can switch off:** API access control and app allowlisting: "Google Workspace
  administrators can control access to connected applications through allowlisting." [doc:
  WS-POLICY]; "Google Workspace can control access to connected applications via allowlisting."
  [doc: UD-POLICY]. Removing the delegation (google-auth.md). Calendar-specific admin switches
  that would stop reads (e.g. turning Calendar off for a user) and what the API then returns are
  **[not stated]**.
- Focus time, out of office and working location exist only for some users: "These features are
  only available on primary calendars, and to some Google Calendar users." [doc: G-STATUS]

## K. Policy (quoted, not interpreted)

From WS-POLICY ("Google Workspace user data and developer policy"). It has no section for
Calendar scopes; its "Appropriate access" sections cover Gmail, Drive, Chat and Meet only.

- Scope of the policy: it "governs your use and access to Google Workspace developer products and
  services requesting user data, which include Gmail, Google Chat, Google Drive, Google Sheets,
  and other Google Workspace APIs, MCPs, and other developer tools requesting access to user
  data."
- "If an app is only used by users within your own organization, then these additional
  requirements do not apply." (said of the Restricted Scopes' additional requirements)
- Limited use: "These requirements apply to data derived from both Sensitive and Restricted
  scopes."
- "Limit your use of data to providing or improving your appropriate use case or features that are
  visible and prominent in the requesting application's user interface."
- "Transfers of data are not allowed, except:" and the first exception: "To provide or improve
  your appropriate use case or user-facing features that are visible and prominent in the
  requesting application's user interface and only with the user's consent;" (the others: security
  purposes, applicable laws, a merger with the user's prior consent).
- Humans reading data: "Do not allow humans to read user data, unless:" and the first exception:
  "You have obtained and documented the user's explicit consent or affirmative agreement to view
  specific messages, files, or other data".
- AI and machine learning, in the list of prohibited uses: "Transferring, selling, or using user
  data to create, train, or improve a machine learning or artificial intelligence model beyond that
  specific user’s personalized model for the appropriate use case or user-facing feature."
- "Note: Remember, our Terms of Service prohibits the scraping, building databases (including
  databases for model training purposes), or otherwise creating permanent copies of Google User
  data. This includes keeping cached copies longer than permitted by the cache header."
- "Treat all user data securely in transit and at rest."
- Required measures for Restricted Scopes include encrypting user data "At rest on your systems",
  "Keeping user data and credentials, specifically tokens such as OAuth access and refresh tokens,
  encrypted at rest", and "Protecting against prompt injection techniques by either using Google
  Cloud Platform's Model Armor or other prompt injection protection."
- Consent: "You must not begin collection prior to obtaining affirmative consent." and "You must
  provide user help documentation that explains how users can manage and delete their data from
  your app or service."
- Agentic requests: "For example, granularly require users to confirm the MCP, tool, skill, or
  other agentic behavior invocation."
- Suggested disclosure: "The use of information received from Google Workspace scopes will adhere
  to the Google User Data Policy, including the Limited Use requirements."

From UD-POLICY ("Google API Services User Data Policy"):

- "Note: If your app is only used by users within your own domain, then these requirements do not
  apply." (said of the "Additional Requirements for Specific API Scopes").
- Limited Use "requirements apply to the raw data obtained from the scopes and data aggregated,
  anonymized, or derived from them."
- "Your privacy policy and in-product privacy notifications must thoroughly disclose the manner in
  which your application accesses, uses, stores, or shares Google user data."
- "You are not permitted to access, aggregate, or analyze Google user data if the data will be
  displayed, sold, or otherwise distributed to a third party conducting surveillance."

From CLOUD-VERIFY (on AI, paraphrased): an application that transfers data for AI model training,
within the approved use cases, must show in its demonstration video the step where the user
explicitly consents to their data being used for training a personalized AI model.

From TOOLS-SAFETY: "Note that working with Google Workspace APIs is still subject to the Google
Workspace API developer policy and the Google APIs Terms of Service." From RELEASES (October 12,
2026), for MCP only: "Apps and integrations that are not AI agents, like those that only call
Google Workspace REST APIs, are not affected by this change."

---

## Open questions for the connector

1. QUESTION: How does the connector find the events that ended since the last run? The only tools
   are `timeMin` (exclusive bound on *end*), `timeMax` (exclusive bound on *start*) and the clock;
   in-progress events come back and must be deferred; `syncToken` cannot be combined with either
   bound and does not report an event merely ending (B, G, headline 1). Decide the watermark (end
   time of the last event read, or the run's `now`) and how an event that ends exactly at the
   watermark is handled given "exclusive".
   **[cairn]** The run's `now`. Each pull lists `timeMin` two days before the start of the last
   pull that finished (the first pull's mark is the switch-on) and `timeMax` the pull's own
   start, with `singleEvents=true` and `orderBy=startTime`. An event has ended when its `end`
   is at or before the pull's start; an all-day event at 00:00 on `end.date` in the listing's
   `timeZone` (12:00 UTC on it when the zone is absent or unknown). The two days' overlap and
   a digest of each event's id written, kept while the listing can still return the event,
   mean an event at the bound is read once.
2. QUESTION: Can a full sync bounded by `timeMin` produce a sync token that is then used without
   bounds, as G-SYNC's sample does, or does that hit EV-LIST's "undefined behavior" (G, CONFLICT)?
   Only matters if sync tokens are used to see edits or deletions after an event was read.
   **[cairn]** Moot: no sync token is used.
3. QUESTION: What is the `id` of an instance of a recurring event, and is it stable when that
   instance is edited or moved? cairn's `source_ref` for calendar items is
   `calendar:<event id>`; the docs say only that occurrences have different ids (F).
   **[cairn]** The `id` is written as Google gives it, and what has been read is keyed by its
   digest. An instance whose `id` changed would be a new event to cairn and read again.
4. QUESTION: Which scope class does Google assign to `calendar.events.readonly`,
   `calendar.events.owned.readonly` and `calendar.calendarlist.readonly`? Only the Cloud console
   shows it (A). It decides verification for a non-internal app and whether Limited Use applies.
   **[cairn]** Not answered: cairn asks for `calendar.events.readonly` only, and its guide says
   nothing of the scope's class.
5. QUESTION: Does `calendar.events.owned.readonly` cover only the primary calendar and calendars
   whose data owner is the person, or every calendar where the person has the `owner` role (A)?
   If operator-named calendars are shared calendars, `calendar.events.readonly` is needed.
   **[cairn]** `calendar.events.readonly`, so a calendar the account only reads can be named.
6. QUESTION: For a Meet conference, which fields carry the code: `conferenceId` (stated),
   `entryPoints[].meetingCode`, the `video` entry point's `uri`/`label`, `hangoutLink` (D)? And
   since a code can sit on several events (copied `conferenceData`, every instance of a series)
   and be reused later (Meet), how does the sibling transcript source pick the right event: by
   code plus time overlap?
   **[cairn]** The note's Meet line is `conferenceId` as Google gives it, for a
   `conferenceSolution.key.type` of `hangoutsMeet` only, and only when it holds no white space
   and no "@" (at most 128 characters). The other fields are not read, and no join to a
   transcript is made.
7. QUESTION: Is the Meet transcript linked to the event as an `attachments[]` entry, and is its
   `fileId` the transcript's Docs id (D)? That would be a firmer join than the meeting code.
   **[cairn]** Attachments are not read.
8. QUESTION: Invitee names: `displayName` is "if available" and `email` may be a generated,
   non-working value (D). Does the note fall back to the email, and should rooms
   (`resource: true`) and the person's own entry (`self: true`) be listed? What does a guest's copy
   show when `guestsCanSeeOtherGuests` is false (not stated)?
   **[cairn]** No fallback to the email: attendees are listed by `displayName` in Google's
   order, and one with none, or with a name holding "@", is counted ("and N more"). Rooms
   (`resource: true`) are left out; the person's own entry is listed like anyone's. The
   organiser is named only by `displayName`. Whatever Google returns is what is read.
9. QUESTION: Private events on calendars the person only reads: the docs disagree on who sees
   details (attendees vs `writer`) and do not show the hidden-details JSON (E). Skip
   `visibility: private` events, or write what is visible?
   **[cairn]** Skipped, silently: `private` and `confidential` events are passed over on every
   calendar, whatever the reader's role.
10. QUESTION: Which `eventTypes` to read: `default` only, or also `fromGmail` (flight and
    reservation events from mail) (E)? Out-of-office, focus time, working location and birthdays
    are probably not meetings.
    **[cairn]** `default` only (or no `eventType` at all). The filter is cairn's, not
    `eventTypes`, so `fromGmail` and any type Google adds later are passed over.
11. QUESTION: Are invitations the person has not answered, and that their settings keep off the
    calendar, returned by `events.list` at all, or only with `showHiddenInvitations=true`, whose
    meaning is undocumented (E)?
    **[cairn]** `showHiddenInvitations` is not sent: what `events.list` returns without it is
    read. An event the calendar's own attendee (`self`) declined is passed over.
12. QUESTION: `timeMin` is exclusive on `events.list` and inclusive on `events.instances` (F).
    Moot if `instances` is not used; otherwise which is true?
    **[cairn]** Moot: `instances` is not used.
13. QUESTION: Under domain-wide delegation, quota is charged to the service account as one user
    (600/minute) unless `quotaUser` is sent with an IP-restricted API key (I). Is one person per
    source small enough to ignore this, and does the server have a fixed public IP if not?
    **[cairn]** No `quotaUser` is sent. One source reads one calendar with at most 100
    requests a pull; a rate limit is waited out four times, then the next run goes on.
14. QUESTION: Error bodies for a token lacking the scope, for a calendar that was deleted, and the
    400 for a forbidden parameter with `syncToken`; and whether 403/429 carry `Retry-After` (H).
    **[cairn]** Read by status and `reason` alone: 401, and a 403 that is neither a rate limit
    nor `quotaExceeded`, fail the pull with what to check (the scope, the delegation, the API
    enabled, access to `calendar_id`); 404 names `calendar_id`. `Retry-After` is not read: the
    wait is 2^n seconds and up to one more.
15. QUESTION: How long does a sync token live, and how far back may `updatedMin` go before
    `updatedMinTooLongAgo` (G, H)?
    **[cairn]** Moot: neither `syncToken` nor `updatedMin` is sent.
16. QUESTION: `endTimeUnspecified: true`: what is in `end`, and when has such an event "ended" (E)?
    **[cairn]** The flag is not read: such an event has ended when the `end` Google gives has
    passed, as any other.
17. QUESTION: `description` "Can contain HTML": convert to text or Markdown before writing the
    note, and is it ever plain text with escaped entities (D)?
    **[cairn]** To text, with `cairn.sources.markup.html_to_text`; a description with no tag
    is read as plain text, its entities decoded.
18. QUESTION: An event deleted after the source read it, or edited later: cairn never takes back a
    note it has stored (its sources guide). Does the connector ever re-read an event it has written, or is reading it once, after it
    ended, final (E)?
    **[cairn]** Final: an edit or a deletion afterwards changes nothing. One exception follows
    from keeping digests only while the listing can return the event: an event moved more than
    about two days later after it was read is read again once it ends at its new time.
19. QUESTION: Has charging for use above the daily threshold started (I)?
20. QUESTION: How does the operator name a calendar: by its id (an email address) or by its
    title? A title lookup goes through `calendarList.list`, which has no name filter, may hold two
    calendars with the same title, and leaves out calendars shared with the person but never added
    to their list (C). An id needs no lookup and no `calendarlist` scope.
    **[cairn]** By its id, `calendar_id`, `primary` unless given, percent-encoded in the path.
    No lookup and no `calendarlist` scope.
