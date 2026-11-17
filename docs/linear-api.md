# Linear GraphQL API for a scheduled reader of finished issues (personal API key, read-only)

Researched 2026-11-14. No Linear API was called, no introspection query was run, nothing was
signed in to. Every page below was fetched that day with `curl` (User-Agent `curl/8`); Linear
serves each docs page as Markdown when `.md` is appended (stated in LLMS), and that Markdown
form was saved under `notes/linear/raw/`, copied to `notes/linear/txt/` and read in full unless
its line says otherwise. Linear's pages show no visible "Last updated" line; the dates given
below are the CMS `_updatedAt` values embedded in the HTML of the docs site's navigation
(read from `raw/developers_graphql.html` and `raw/docs_private-teams.html`), not dates Linear
displays. Legal pages carry their own "Effective date".

Developer docs (base https://linear.app/developers):

- **GRAPHQL** = .../graphql.md "Getting started" (CMS 2025-11-14). The old URL
  https://developers.linear.app/docs/graphql/working-with-the-graphql-api redirects to
  https://linear.app/developers.
- **PAGINATION** = .../pagination.md (2025-04-30)
- **FILTERING** = .../filtering.md (2025-04-30)
- **RATE-LIMITS** = .../rate-limiting.md (2026-11-13)
- **DEPRECATIONS** = .../deprecations.md (2025-04-30)
- **WEBHOOKS** = .../webhooks.md (2026-09-01)
- **ATTACHMENTS** = .../attachments.md (2025-04-30)
- **FILES** = .../file-storage-authentication.md (2025-07-02)
- **OAUTH** = .../oauth-2-0-authentication.md (2026-11-07)
- **OAUTH-ACTOR** = .../oauth-actor-authorization.md (2026-11-07)
- **SDK** = .../sdk.md (2025-05-22); **SDK-FETCH** = .../sdk-fetching-and-modifying-data.md
  (2025-05-22); **SDK-ERRORS** = .../sdk-errors.md (2025-04-30); **SDK-ADV** =
  .../advanced-usage.md (2026-01-07)

The schema and the SDK source (the API's machine-readable reference; Linear has no discovery
document):

- **SCHEMA** = https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/schema.graphql,
  linked from LLMS as "GraphQL SDL Schema Definition" [doc: LLMS] and from SDK. 46,122 lines. The last commit
  touching the file is `87d638c0b151` of 2026-11-11T19:41:49Z (message `chore(release): update schema and
  version packages (#1333)`). This is the SDK's copy of the schema, not an introspection result;
  whether it equals what `api.linear.app` serves on a given day is **[not stated]**. Its type and
  field descriptions are long and specific, and many facts below rest on them. Tag:
  **[schema: Type.field]**.
- **SDK-SRC** = `error.ts`, `types.ts`, `graphql-client.ts`, `client.ts` from
  https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/ (the folder SDK-ADV
  links to; `error.ts` last changed in `f3685705dcca`, 2026-10-08). Code, not prose: it shows
  what Linear's own client sends and expects. Tag: **[source: SDK-SRC]**.
- The two commit ids and dates above came from two unauthenticated GETs to
  `api.github.com/repos/linear/linear/commits?path=...` (GitHub's public API, not Linear's;
  nothing identifying sent).

Product docs (base https://linear.app/docs):

- **API-DOCS** = .../api-and-webhooks.md (2025-09-02)
- **SEC-ACCESS** = .../security-and-access.md (2026-06-26)
- **PRIVATE-TEAMS** = .../private-teams.md (2026-07-27)
- **TEAMS** = .../teams.md (2026-11-02); **SUB-TEAMS** = .../sub-teams.md (2026-11-04)
- **MEMBERS** = .../members-roles.md (2026-11-02)
- **APP-APPROVALS** = .../third-party-application-approvals.md (2026-09-01)
- **DELETE-ARCHIVE** = .../delete-archive-issues.md (2026-11-04)
- **ISSUE-PROPERTY** = .../issue-property.md (no CMS date found). Reached this way:
  `/docs/configuring-workflows` answers 307 to `/docs/issue-properties-home`, a hub whose `.md`
  form is empty; its HTML holds a card "Issue status" linking to `/docs/issue-property#issue-status`.
- **COMMENTS** = .../comment-on-issues.md (2026-06-17)
- **EDITOR** = .../editor.md (2026-09-07)
- **ASSIGN** = .../assigning-issues.md (2026-09-12)
- **UPDATES** = .../initiative-and-project-updates.md (2026-11-13)
- **EXPORT** = .../exporting-data.md (2026-06-25)
- **AUDIT** = .../audit-log.md (2026-06-03)
- **SECURITY** = .../security.md (2026-09-22)
- **WORKSPACES** = .../workspaces.md (2026-10-09); **CONCEPTS** = .../conceptual-model.md
  (2026-10-13); **PROFILE** = .../profile.md (2026-10-21); **PREFS** = .../account-preferences.md
  (2026-08-21)

Other Linear pages:

- **LLMS** = https://linear.app/llms.txt (index; used to find pages)
- **CHANGELOG** = https://linear.app/changelog.md (entries from November 12, 2026 back to July 30,
  2026). Keyword-searched only (API, rate limit, 429, webhook, API key, includeArchived, trash);
  the entries cited (now paraphrased) were read with their date headers.
- **TERMS** = https://linear.app/terms.md ("Effective date: June 09, 2026"). Sections 1, 2, 3,
  4.1 to 4.3, 9 and 10 read in full; the rest of the file (4.4 to 8, 11) only keyword-searched
  (AI, artificial, machine learning, API, automat, scrap, model, terminat, retriev, delet, export).
- **AUP** = https://linear.app/legal/aup.md ("Effective date: June 09, 2026"), read in full.
- **AI-ADDENDUM** = https://linear.app/legal/ai-addendum.md ("Effective date: June 09, 2026").
  Headings read; keyword-searched (API, train, model, third-party, export); clauses 1.2, 13.2, 13.3
  and 17.2 read.
- **LEGAL** = https://linear.app/legal.md (index of the legal pages).
- Keyword-searched only (API, train, model, AI, automat, scrap, key, export), nothing used:
  https://linear.app/privacy.md ("Effective date: November 05, 2026"), https://linear.app/dpa.md
  ("Signed: May 31, 2025"), https://linear.app/ai.md (a product page; it mentions building agents
  with the API and the MCP server, nothing on terms of data use), https://linear.app/security.md
  (a product page; nothing on the API).

Not fetched or not found: the Apollo Studio explorers for the API and for webhook payloads
(script-rendered apps, linked from GRAPHQL and WEBHOOKS); `.../developers/llms.txt` (404);
`/docs/issue-status`, `/docs/workflows`, `/docs/issue-status-and-workflows` (guessed, 404);
`/docs/configuring-workflows.md` (404; see ISSUE-PROPERTY). Not fetched as out of scope: the agent,
SDK-webhook, OAuth-manifest, customers, SCIM, SAML, login-methods, billing and triage pages.

Tags: **[doc: X]** = stated in prose on page X; **[example: X]** = only visible in an example
request, response or query on page X; **[schema: T.f]** = the description or type of field `f`
of type `T` in SCHEMA; **[source: SDK-SRC]** = behaviour of Linear's own TypeScript client;
**[not stated]** = nowhere in the pages above. **CONFLICT** = two Linear statements that pull
against each other, both given. **QUESTION:** = a gap that matters for the connector's design.
Note on GraphQL itself: a GraphQL server returns only the fields a query selects. That is the
GraphQL specification, not a Linear statement; Linear's pages do not repeat it.

---

## Headline points (read first)

1. **Forward pages are newest first, and `orderBy` knows only `createdAt` and `updatedAt`.**
   `PaginationOrderBy`: paging forward gives the newest results first (paraphrased). [schema:
   PaginationOrderBy] On every list: newest first with `first`, oldest first with `last`
   (paraphrased) [schema: Query.issues.orderBy]. The Pagination page says only that results are
   ordered by `createdAt` by default, with no direction (paraphrased) [doc: PAGINATION]. There is no
   `completedAt` or `canceledAt` order in `orderBy`; `sort` has a `completedAt` key (none for
   `canceledAt`), and `orderBy` may not be used together with a custom `sort` on the fields that
   have one (paraphrased) [schema: Query.issues.orderBy]. The tie-break between equal timestamps is
   **[not stated]**. This also applies to an issue's `comments`.
2. **There are seven workflow state types, and a duplicate counts as canceled in the timestamps.**
   `type` takes one of `triage`, `backlog`, `unstarted`, `started`, `completed`, `canceled`,
   `duplicate` [schema: WorkflowState.type]. `canceledAt` is set when the issue enters a canceled or
   duplicate status and cleared when it leaves those types; `completedAt` is set when it enters a
   completed status and is null while it is not completed (paraphrased). [schema: Issue] The state's `name`
   is free text per team; filter on `type` [schema: IssueFilter.state].
3. **Rate-limit errors are now HTTP 429; before, they were 400.** CHANGELOG, in the entry dated
   November 5, 2026 (a release-note date, not a stated switch-over time), paraphrased: GraphQL
   rate-limit errors now answer HTTP 429 where they answered 400, and the body still carries the
   `RATELIMITED` error code. [doc: CHANGELOG]
   RATE-LIMITS now says 429 plus `Retry-After` in seconds. Personal-key budget: 2,500 requests
   and 3,000,000 complexity points per hour, per user, shared by all that user's keys
   [doc: RATE-LIMITS]; one query may not exceed 10,000 points.
4. **Archived issues are hidden by default, and finished issues are archived automatically.**
   Archived issues are left out unless `includeArchived` is true (paraphrased). [schema: Query.issues] Closed
   issues are auto-archived after a team-set period [doc: DELETE-ARCHIVE]. Deleted (trashed)
   issues: `includeArchived` leaves out deleted resources (paraphrased) [schema: Query.issues], yet
   a trashed issue is archived as well (paraphrased) [schema: Issue.trashed]. Whether a trashed issue is listed
   with `includeArchived: true` is a QUESTION (section F).
5. **A personal API key is sent without `Bearer`; an OAuth token with it.** The API key goes in
   the header as `Authorization: <API_KEY>`, an OAuth access token as
   `Authorization: Bearer <ACCESS_TOKEN>` (paraphrased)
   [doc: GRAPHQL]. A key can be limited to the Read permission and to chosen teams
   [doc: API-DOCS]. Its lifetime, prefix and format are **[not stated]**.
6. **`identifier` (ENG-123) is not stable; `id` is.** The identifier changes when the issue moves
   to another team, and `id` is the one to keep (paraphrased). [schema: Issue.identifier]
7. **Emails are a selectable field on `User` (`email: String!`) and also appear in webhook and
   audit-log payload examples.** A query that does not select `email` does not receive it
   (GraphQL); see section I for every place an email can surface.

---

## A. Endpoint, request, authentication

- Endpoint: `https://api.linear.app/graphql`; it answers introspection, so the whole schema can be
  queried (paraphrased). [doc: GRAPHQL]
- Request: `POST`, `Content-Type: application/json`, body `{"query": "..."}` [example: GRAPHQL].
  Linear's client sends `JSON.stringify({ query, variables })` with `method: "POST"` and
  `Content-Type: application/json` [source: SDK-SRC graphql-client.ts]. An `operationName`
  member is never shown or mentioned: **[not stated]**. GET requests: **[not stated]**.
- Personal API key: requests are authenticated by sending the key in the header
  `Authorization: <API_KEY>` (paraphrased) [doc: GRAPHQL]; the custom-client example sets
  `headers: { Authorization: apiKey }` [example: SDK-ADV]. Linear's client sends the key as is
  and an OAuth token as `Bearer <token>`; a code comment there says it uses bearer when an OAuth
  token is present and the API key otherwise (paraphrased) [source: SDK-SRC client.ts]. Whether
  `Bearer <API_KEY>` is also accepted is **[not stated]**.
- OAuth access token: sent in the header `Authorization: Bearer <ACCESS_TOKEN>` (paraphrased)
  [doc: GRAPHQL]; OAUTH gives the same header as an alternative (paraphrased) [doc: OAUTH].
- Linear's client refuses a non-HTTPS URL except localhost: its error says the client's `apiUrl`
  has to be HTTPS unless it names a local development server (paraphrased) [source: SDK-SRC
  client.ts]. It sets its own
  `User-Agent` [source: SDK-SRC client.ts]; any User-Agent requirement is **[not stated]**.
- Versioning (paraphrased): the GraphQL API has no versions, as many REST APIs do, because GraphQL
  changes all the time. Deprecations use `@deprecated`; in other cases a feature is removed and a
  stub that does nothing is left in its place so that queries and mutations do not break. API
  changes are listed in the Linear changelog with an `[API]` prefix [doc: DEPRECATIONS]. (The
  changelog read uses an "API" bullet heading and
  unprefixed bullets, not a literal `[API]` prefix [doc: CHANGELOG].)
- Request size limits, compression, timeouts, request-id headers: **[not stated]**. The only
  size limit is query complexity (section G).
- Unauthenticated requests are possible and have their own limits (600 requests per hour per IP)
  [doc: RATE-LIMITS]; which fields they can read is **[not stated]**.

## B. Responses and errors

### B.1 Shape

- Errors follow the standard GraphQL form (paraphrased): an `errors` array in the response, each
  error with a `message` saying what went wrong, a `path` array saying where in the query, and
  `extensions` that may hold more context, such as error codes or validation details.
  [doc: GRAPHQL]
- Partial data (paraphrased): a query can partly succeed with HTTP 200, returning some data and
  errors for the fields that failed; server errors (5xx) are to be watched for by HTTP status
  [doc: GRAPHQL].
- The only full error body in the docs is the rate-limit one [example: RATE-LIMITS]:
  ```json
  { "errors": [ { "message": "...", "extensions": { "code": "RATELIMITED", ... } } ] }
  ```
- What Linear's client reads from an error (keep separate from the docs example above; neither
  confirms the other) [source: SDK-SRC types.ts, error.ts]:
  ```
  LinearGraphQLErrorRaw:
    message?:  LinearErrorType   (declared so in types.ts; used as a string)
    path?:     string[]
    extensions?: { type?: LinearErrorType, userError?: boolean, userPresentableMessage?: string }
  ```
  (`LinearErrorType` is the SDK's TypeScript enum; the comparison is against the lowercase
  strings listed next.)
  `extensions.type` is matched against these strings: `feature not accessible`, `invalid input`,
  `ratelimited`, `network error`, `authentication error`, `forbidden`, `bootstrap error`,
  `unknown`, `internal error`, `other`, `user error`, `graphql error`, `lock timeout`,
  `usage limit exceeded`. The client reads `extensions.type`, not `extensions.code`.
  Whether a real error carries `code`, `type`, or both, and their exact values for an invalid
  key, a forbidden team, or a missing issue, is **[not stated]**. QUESTION.
- A non-JSON error body is possible as far as Linear's client is concerned: it parses JSON only
  when `Content-Type` starts with `application/json`, else keeps the text as `error`
  [source: SDK-SRC graphql-client.ts]. When Linear sends such a body is **[not stated]**.

### B.2 HTTP statuses

| Status | Where stated | Meaning |
|---|---|---|
| 200 | [doc: GRAPHQL] | success, or partial success with `errors` |
| 429 | [doc: RATE-LIMITS, CHANGELOG] | rate limited; body `RATELIMITED`; `Retry-After` seconds |
| 400 | [doc: CHANGELOG] | rate limit errors before 2026-11-05 (see C) |
| 401 | [doc: OAUTH] | a client-credentials token that is no longer valid: on a 401 the server is to fetch a new token (paraphrased) |
| 5xx | [doc: GRAPHQL] | server errors, to be watched for by HTTP status (paraphrased) |

- Linear's client maps a response without GraphQL errors by status: 403 to `Forbidden`, 429 to
  `Ratelimited`, any other 4xx to `AuthenticationError`, 500 to `InternalError`, other 5xx to
  `NetworkError` [source: SDK-SRC error.ts]. That is the client's fallback, not a documented list.
- The status and body for a missing, malformed or revoked personal API key: **[not stated]**.
  For a GraphQL validation error (unknown field, bad argument): **[not stated]**. QUESTION.
- `issue(id:)`: the query fails when the issue does not exist or the caller cannot reach it
  (paraphrased). [schema: Query.issue] The error's code and HTTP status are **[not stated]**; the
  return type is `Issue!` (non-null).
- OAuth revocation endpoint statuses: `200` revoked, `400` the token could not be revoked (for
  example, it was revoked already), `401` the token could not be authenticated (paraphrased)
  [doc: OAUTH].

## C. Rate limits and complexity

- Algorithm: the page names the "leaky bucket" algorithm (as a link): tokens refill at the
  constant rate `LIMIT_AMOUNT / LIMIT_PERIOD` (paraphrased). [doc: RATE-LIMITS]
- Request limits [doc: RATE-LIMITS]:

| Authentication | Limit | per | Period |
|---|---|---|---|
| API key | 2,500 | User | 1 hour |
| OAuth App | 5,000 | User (or App User) | 1 hour |
| Unauthenticated | 600 | IP Address | 1 hour |

  Requests count against the authenticated user, so every request by one user draws on one quota,
  whichever of that user's API keys it uses (paraphrased). [doc: RATE-LIMITS]
- Complexity limits per hour [doc: RATE-LIMITS]: API key 3,000,000 per user; OAuth app 2,000,000
  per user (or app user); unauthenticated 100,000 per IP. A single query may never exceed
  **10,000 points**; one that does is always rejected (paraphrased). (The error for that
  rejection is **[not stated]**.)
- Plan multipliers: Free and Basic have the standard limits; Business twice the standard limits;
  Enterprise five times (paraphrased) [doc: RATE-LIMITS]. On top of the plan multipliers, limits
  are raised further, by the workspace's number of paid users, for workspace-level OAuth apps that
  use Actor Authorization (paraphrased). [doc: RATE-LIMITS]
- Response headers on every request [doc: RATE-LIMITS]: `X-RateLimit-Requests-Limit` (per
  hour), `X-RateLimit-Requests-Remaining`, `X-RateLimit-Requests-Reset` (UTC epoch
  milliseconds); `X-Complexity`, `X-RateLimit-Complexity-Limit`,
  `X-RateLimit-Complexity-Remaining`, `X-RateLimit-Complexity-Reset` (epoch milliseconds).
  Endpoint-specific limits add `X-RateLimit-Endpoint-Requests-Limit`, `-Remaining`, `-Reset` and
  `X-RateLimit-Endpoint-Name`; each endpoint's window can differ and is given in the response
  body (paraphrased). The body's form for that is **[not stated]**.
- The limit error (paraphrased): a GraphQL request over a rate limit answers HTTP `429`; the
  `errors` field of the body holds the `RATELIMITED` error code, and the `Retry-After` header
  gives the seconds to wait before trying again. [doc: RATE-LIMITS] Linear's client reads `retry-after` and the six
  `x-ratelimit-requests-*` / `x-ratelimit-complexity-*` headers from a rate-limited response
  [source: SDK-SRC error.ts].
- History: until the November 5, 2026 changelog entry the status was 400 (paraphrased in headline 3)
  [doc: CHANGELOG]. A fake should answer 429; a client that also treats 400 + `RATELIMITED` as a
  rate limit is tolerant of the old behaviour (this sentence is mine, not Linear's).
- Backoff: beyond `Retry-After`, Linear's pages give no backoff schedule: **[not stated]**.
  Higher limits for a while can be asked of Linear support (paraphrased) [doc: RATE-LIMITS].
- A query reports the caller's quota: `rateLimitStatus` returns the quota recorded for the
  request, both request count and complexity, and itself uses quota (paraphrased). Fields
  `identifier`, `kind` (OAuth app, API key, Access key, JWT, Custom or Default), `limits[]` with
  `allowedAmount`, `period` (refill period in milliseconds),
  `remainingAmount`, `requestedAmount` [schema: Query.rateLimitStatus, RateLimitPayload,
  RateLimitResultPayload].
- Other per-operation limits seen: `searchIssues` allows 30 requests per minute
  [schema: Query.searchIssues]; creating comments on one issue is limited to 200 per minute
  [doc: CHANGELOG]; OAuth token requests that are abused get `429 temporarily_unavailable` with a
  `Retry-After` header (paraphrased) [doc: CHANGELOG, November 12, 2026].
- Complexity rule (paraphrased): a property costs 0.1 point and an object 1 point; a connection
  multiplies its children's cost by its pagination argument, or by 50 by default; the total is
  rounded up to a whole number. Worked examples on the page: `user(id:
  "me") { name }` is 2; `createdIssues` with 3 fields at the default 50 is 66; with `first: 10`
  it is 14 [doc: RATE-LIMITS].
  - Computed here, not from the page (an estimate; whether `nodes`, `pageInfo` and `edges` count as
    objects is **[not stated]**): 50 issues, each with about 15 scalar fields, five single
    objects (state, team, project, assignee, creator) of one or two fields each, `labels(first:
    20)` with one field and `comments(first: 50)` with four fields and `user { displayName }`,
    comes to about 50 x 155 = 7,750 points. The same query with `first: 100` issues is about
    15,500 and would be rejected. Nested connections dominate the cost.
- Policy on polling (paraphrased): Linear particularly discourages polling the API for updates
  and points to its Webhook feature for learning when data changes [doc: RATE-LIMITS]
  **CONFLICT** (of tone, not of rule): GRAPHQL's "Fetching
  Updates" section says that when polling for recent changes cannot be avoided, results should be
  ordered with the most recently updated issue first, and lists polling each issue for updates as
  a "Don't" (paraphrased). [doc: GRAPHQL]
  RATE-LIMITS also advises, where all data has to be fetched, sorting by the updated timestamp
  rather than the creation time (paraphrased). [doc: RATE-LIMITS]

## D. Listing issues

### D.1 Entry points

- `issues(...)`: lists the issues the caller can reach; `filter` narrows by team, assignee,
  status, labels, project or cycle, the cursor arguments fetch later pages, and archived issues
  are left out unless `includeArchived` is true (paraphrased). [schema: Query.issues]
- `team(id:) { issues(...) }`: the team's issues, with filtering and, optionally, sub-team issues
  (paraphrased). Extra argument `includeSubTeams: Boolean = false`, which adds the sub-teams'
  issues. [schema: Team.issues]
- `project(id:) { issues(...) }`: the project's issues. [schema: Project.issues]
  `project(id:)` takes the project's UUID, slug ID or human-readable identifier.
  [schema: Query.project]
- `workflowState(id:) { issues }`, `user(id:) { assignedIssues / createdIssues }` exist
  [doc: GRAPHQL; schema: User].
- `issue(id:)`: finds an issue by UUID or by a human-readable identifier such as ENG-123, and can
  return archived issues too (paraphrased). [schema: Query.issue] Whether a trashed issue can be returned is
  **[not stated]**.
- `searchIssues` is relevance search over text, allowed 30 requests per minute
  [schema: Query.searchIssues]; `issueSearch` is marked `[DEPRECATED]`. Neither is a listing.
- `teams`: every team whose issues the user can reach, that is the public teams and the private
  teams the user belongs to (paraphrased). [schema: Query.teams]

### D.2 Arguments (same on every connection read here) [schema: Query.issues and siblings]

| Argument | Type | Description (paraphrased) |
|---|---|---|
| `first` | Int | Paraphrased: how many results at most, after `after` when given; 1 to 250; 50 when neither `first` nor `last` is given; not with `last` or `before`. |
| `after` | String | Paraphrased: results after this cursor; used with `first` and the previous page's `endCursor` for the next page; filters and ordering stay the same from page to page; not with `last` or `before`. |
| `last` | Int | Paraphrased: how many results at most, ending before `before` when given; 1 to 250; not with `first` or `after`. |
| `before` | String | Paraphrased: results before this cursor; used with `last` to page backward, with a cursor from the same connection. |
| `filter` | IssueFilter | Paraphrased: filters the issues returned. |
| `includeArchived` | Boolean | Paraphrased: adds archived resources to active ones; false by default, which gives active resources only; deleted resources are not included. |
| `orderBy` | PaginationOrderBy | Paraphrased: orders by creation time or by last meaningful update; `createdAt` by default; newest first with `first`, oldest first with `last`, the returned `endCursor` going into `before` to continue backward; not together with a custom `sort` on fields that have one. |
| `sort` | [IssueSortInput!] | Paraphrased: sorts the issues returned. (`Query.issues` has it; `Team.issues`, `Project.issues` and `Issue.comments` do not; `users` and `projects` have their own `sort` inputs) |

- Default page size 50 is also in the docs: without query arguments the first 50 results come
  back (paraphrased). [doc: PAGINATION] The maximum, 250, is only in SCHEMA.
- `PaginationOrderBy` values: `createdAt` (by creation time) and
  `updatedAt` (by last meaningful update) [schema].
- `IssueSortInput` keys include `completedAt` (`CompletedAtSort`), `createdAt`, `updatedAt`,
  `workflowState` (by workflow state type), `priority`, `title` and others; each takes
  `order: PaginationSortOrder` (`Ascending`, `Descending`) and `nulls: PaginationNulls = last`
  [schema: IssueSortInput, CompletedAtSort, PaginationSortOrder, PaginationNulls]. There is no
  `canceledAt` sort [schema]. Whether `sort` pages stably with `after`, and its tie-break, are
  **[not stated]**. The docs pages never mention `sort` [doc: PAGINATION, FILTERING].
- `Team.issues`, `Project.issues` and `Issue.comments` take the same arguments without `sort`
  (`Team.issues` adds `includeSubTeams`) [schema].

### D.3 Filtering by team, project, state type and time

- `IssueFilter` (paraphrased): the conditions that select issues; the fields given are joined
  with AND, `or` takes alternatives and `and` groups conditions. [schema: IssueFilter]
  - `team: TeamFilter` (by `id`, `key` (the prefix of the team's issue identifiers),
    `name`, `parent`, ...) [schema: IssueFilter.team, TeamFilter].
  - `project: NullableProjectFilter` (by `id`, `slugId`, `name`, `status`; `null: true` for issues
    without a project) [schema: IssueFilter.project, NullableProjectFilter].
  - `state: WorkflowStateFilter` (paraphrased): filtering by type, such as started or completed,
    matches one workflow category across teams whose status names and IDs differ. `type` is a
    `StringComparator` taking `triage`, `backlog`, `unstarted`, `started`,
    `completed`, `canceled` or `duplicate`. [schema: IssueFilter.state, WorkflowStateFilter.type]
    Example shape: `state: { type: { eq: "started" } }` [example: FILTERING].
  - Time fields: `createdAt`, `updatedAt` (`DateComparator`); `completedAt`, `canceledAt`,
    `startedAt`, `archivedAt`, `autoArchivedAt`, `autoClosedAt`, `triagedAt`, `addedToCycleAt`,
    `slaBreachesAt`, `snoozedUntilAt`, `accumulatedStateUpdatedAt` (`NullableDateComparator`)
    [schema: IssueFilter]. `archivedAt` (paraphrased): `includeArchived` has to be set on the
    query as well, since the filter alone does not bring back what the query's archive setting
    leaves out. [schema:
    IssueFilter.archivedAt] `accumulatedStateUpdatedAt` is described only as a comparator for the
    issue's `accumulatedStateUpdatedAt` date, and no field of that name is on `Issue`: meaning **[not stated]**.
- Comparators [schema: DateComparator, NullableDateComparator]:
  - `eq`, `neq`, `in`, `nin`, `gt`, `gte`, `lt`, `lte`; nullable ones add `null: Boolean`.
  - Bounds are explicit: `gt` matches values greater than the one given; `gte` values greater
    than or equal to it; likewise `lt` / `lte`.
  - DateComparator (paraphrased): compares timestamps to the millisecond; takes ISO 8601
    timestamps or durations counted from when the filter runs, so `{ gte: "-P7D" }` covers the
    last seven days; `gte` with `lt` gives a time window; a YYYY-MM-DD value stands for midnight
    UTC, so equality with a date does not match the whole day.
  - NullableDateComparator (paraphrased): `{ null: true }` matches missing timestamps; `nin`
    matches missing values too, `neq` and the range comparisons do not.
  - The FILTERING page lists the same comparators and says every date field accepts relative
    time, as ISO 8601 durations counted from the current date (paraphrased). [doc: FILTERING]
- Example that combines a date filter and a relation [example: FILTERING]:
  `viewer { createdIssues(filter: { completedAt: { gt: "-P2W" } }) { nodes { id, title } } }`.
- `StringComparator` compares text literally, case-sensitive unless the operator says otherwise
  (paraphrased) [schema: StringComparator].
- Team labels and the API (paraphrased): combining same-named team labels in multi-team filters
  does not work through the API, where each team's label is named by its own unique identifier.
  [doc: ISSUE-PROPERTY]

### D.4 Paging

- Relay style (paraphrased): Relay cursor pagination with `first`/`after` and `last`/`before`;
  for the next page, `pageInfo.endCursor` goes into `after`, for as long as
  `pageInfo.hasNextPage` is true [doc: PAGINATION]. Both `edges { node cursor }` and
  `nodes` forms work [doc: PAGINATION].
- `PageInfo` [schema]: `endCursor: String` (the last result's cursor, used with `after` for the
  next page; null for an empty page), `hasNextPage: Boolean!`, `hasPreviousPage:
  Boolean!` (false on a forward page does not always mean there are no earlier results),
  `startCursor: String` (paraphrased).
- `IssueConnection`: a page may hold fewer results than asked for. `nodes` is empty when nothing
  matches or nothing is left in the direction asked. `IssueEdge.cursor` is opaque and is passed
  unchanged to `before` or `after` (paraphrased). [schema]
- Cursor lifetime, whether it is a snapshot or a live position, what happens to a page sequence
  when issues change between pages, and the error for a stale cursor: **[not stated]**. QUESTION.
- A total count on `IssueConnection`: none [schema]. (`Team.issueCount` exists [schema: Team].)
- Nested connections page separately. On an issue, `comments`, `labels`, `stateHistory`,
  `attachments`, `children` and `history` are connections; `comments`, `labels` and
  `stateHistory` take the same `first` description (1 to 250, 50 when neither `first` nor `last`
  is given) and each has its own `pageInfo` [schema: Issue]. An
  issue with more than the requested number of comments or labels comes back truncated, with
  `hasNextPage: true` on that nested connection; a fake should model that.

### D.5 Incremental reading: what "changed since" means here

- There is no change token, sync cursor or delta feed among the 160 fields of `Query`
  (searched for sync, delta, change, since, event, history, trash, deleted; the matches are
  `auditEntries` (workspace audit log, Enterprise, owners only [doc: AUDIT]),
  `documentContentHistory`, `archivedIntegrations` and `issueImportCheckSync`, none of which is
  an issue change feed) [schema: Query].
- The documented ways to learn about changes are: timestamp filters (`updatedAt`,
  `completedAt`, `canceledAt`, ... with `gt`/`gte`, D.3); `orderBy: updatedAt` (ordering by
  `updatedAt` gives the most recently updated resources, paraphrased [doc:
  PAGINATION]); and webhooks (section L), which RATE-LIMITS prefers over polling (C).
- Cursor lifetime and staleness: **[not stated]** (D.4).
- Because `completedAt` / `canceledAt` are cleared when an issue leaves the type and set again
  when it re-enters (E.1), a time-window filter on them finds a reopened-and-refinished issue
  again; whether the new value is later than the old is **[not stated]** but follows from
  "entered" (inference).

### D.6 Example request (assembled from the shapes above; not a Linear example)

```graphql
query FinishedIssues($teamId: ID!, $since: DateTimeOrDuration!, $after: String) {
  issues(
    first: 50
    after: $after
    includeArchived: true
    filter: {
      team: { id: { eq: $teamId } }
      state: { type: { in: ["completed", "canceled", "duplicate"] } }
      or: [ { completedAt: { gte: $since } }, { canceledAt: { gte: $since } } ]
    }
  ) {
    nodes { id identifier url title completedAt canceledAt state { name type } }
    pageInfo { hasNextPage endCursor }
  }
}
```
Input types checked in SCHEMA: `IDComparator.eq` is `ID` (it compares resource UUIDs; only the
entity-specific ID comparators take human-readable identifiers, paraphrased),
`StringComparator.in` is `[String!]`, the date comparators take `DateTimeOrDuration`
[schema: IDComparator, StringComparator, NullableDateComparator]. Whether this `or` of two
nullable date ranges is served efficiently, or counts extra complexity, is **[not stated]**.

## E. The issue object

### E.1 Fields the connector needs [schema: Issue]

| Field | Type | Description (paraphrased, trimmed) |
|---|---|---|
| `id` | ID! | Paraphrased: the unique UUID; it does not change on a rename or a move, so stored references and mutations use it. |
| `identifier` | String! | Paraphrased: the current human-readable identifier, such as ENG-123; it changes when the issue moves to another team, so `id` is the stable one. |
| `previousIdentifiers` | [String!]! | Paraphrased: the identifiers the issue had before moving between teams. |
| `number` | Float! | Paraphrased: numbers may have gaps. |
| `url` | String! | Paraphrased: the address that opens the issue in Linear; opening it still depends on the viewer's workspace and issue permissions. |
| `title` | String! | The issue's title. |
| `description` | String | Paraphrased: the description in Markdown, null when none is stored. |
| `documentContent` | DocumentContent | Paraphrased: marked `[ALPHA]`; the document content behind the description. |
| `team` | Team! | Paraphrased: the team the issue belongs to. |
| `project` | Project | Paraphrased: null when the issue is in no project. |
| `state` | WorkflowState! | Paraphrased: the status name can be changed; its type gives the workflow category, such as started or completed. |
| `stateHistory` | IssueStateSpanConnection! | Paraphrased: the spans the issue spent in each status, oldest first; entering a status again opens a new span; a span without `endedAt` is still open. |
| `labels` | IssueLabelConnection! | Paraphrased: the issue's labels. |
| `labelIds` | [String!]! | Paraphrased: the UUIDs of the issue's labels. |
| `assignee` | User | Paraphrased: null when nobody is assigned. |
| `delegate` | User | Paraphrased: the agent the work is delegated to. |
| `creator` | User | Paraphrased: null when the creator's account was deleted, or when an integration or a system process made the issue. |
| `botActor` | ActorBot | Paraphrased: the bot that made the issue, if one did. |
| `externalUserCreator` | ExternalUser | Paraphrased: set when an integration (Slack, Intercom and the like) made the issue for someone who is not a Linear user. |
| `comments` | CommentConnection! | Paraphrased: the issue's comments, replies and inline comments on the description included; each comment's `parent` and `children` rebuild the threads. |
| `attachments` | AttachmentConnection! | Paraphrased: the issue's attachments. (external links, section H) |
| `history` | IssueHistoryConnection! | Paraphrased: the changes recorded for the issue. |
| `parent` | Issue | Paraphrased: null for a top-level issue or when the parent cannot be reached. |
| `trashed` | Boolean | Paraphrased: true for a trashed issue, null otherwise; a trashed issue is archived as well and is to be deleted for good once the grace period ends. |
| `priority` / `priorityLabel` | Float! / String! | 0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low. |

Timestamps (all `DateTime`) [schema: Issue]:

| Field | Description (paraphrased) |
|---|---|
| `createdAt` (non-null) | Paraphrased: the recorded creation time in UTC; an import may keep an earlier creation time. |
| `updatedAt` (non-null) | Paraphrased: the last meaningful update in UTC; some background or housekeeping changes leave it as it is. |
| `startedAt` | Paraphrased: when the issue entered a started status; cleared on a move back to triage, backlog or an unstarted status. |
| `completedAt` | Paraphrased: when the issue entered a completed status; null while it is not completed. |
| `canceledAt` | Paraphrased: when the issue entered a canceled or duplicate status; cleared when it leaves those types. |
| `archivedAt` | Paraphrased: when the resource was archived, in UTC; null while active, after an unarchive too. |
| `autoArchivedAt` | Paraphrased: null when the issue was not auto-archived or has been restored since. |
| `autoClosedAt` | Paraphrased: null when the issue was not auto-closed or its status has changed since. |
| `addedToTeamAt` | Paraphrased: tells a move to the team apart from the issue's original creation time. |
| `addedToProjectAt` | Paraphrased: changes with the project, null when the issue has no project. |
| `triagedAt`, `startedTriageAt`, `addedToCycleAt`, `snoozedUntilAt`, `sla*At` | not needed here |

- Format (paraphrased): `DateTime` is an ISO 8601 timestamp string returned in UTC, such as
  `2026-11-06T14:30:00.000Z`; a timestamp sent in has to carry `Z` or a time zone offset.
  [schema: scalar DateTime] Webhook examples show the same millisecond `Z` form
  (`"createdAt": "2020-01-23T12:53:18.084Z"`) [example: WEBHOOKS]. `dueDate` is a `TimelessDate`,
  YYYY-MM-DD with no time or time zone [schema: Issue.dueDate].
- `Team.timezone` (paraphrased): the IANA time zone of the team's schedules, such as
  Europe/Helsinki, America/Los_Angeles by default. [schema: Team.timezone] Timestamps are UTC regardless.
- Which of these a filter can use: all of them (D.3). Which an ordering can use: `orderBy` only
  `createdAt` / `updatedAt`; `sort` also `completedAt` (D.2).
- Whether `completedAt` is the first or the latest entry into a completed status after a
  reopen-and-recomplete, and whether it changes on a move between two completed states (or two
  canceled states), is **[not stated]**; `stateHistory` records each span (E.3). QUESTION.
- What moves `updatedAt` is not enumerated: only that some background or housekeeping changes do
  not move it (paraphrased) [schema]. Whether a new comment, an archive by auto-archive, or a
  label rename moves an issue's `updatedAt` is **[not stated]**.
- Activity grace period (paraphrased): property changes in an issue's first 3 minutes count as
  part of creating it and are not added to the activity log as changes. [doc: GRAPHQL]

### E.2 Workflow states

- `WorkflowState` (paraphrased): a status in a team's workflow, such as Backlog, In Progress or
  Done; names and colors can be changed, while the status type decides behaviour such as whether
  an issue is open or completed; each team has its own status IDs, also when it inherits its
  workflow from a parent team. Fields: `id`, `name` (the human-readable name, for example
  In Progress, Done, Backlog), `type` (seven values, headline 2), `color`,
  `description`, `position`, `team`, `inheritedFrom`, `archivedAt`, `createdAt`, `updatedAt`
  [schema: WorkflowState].
- Product docs (paraphrased): the default workflow is Backlog, Todo, In Progress, Done, Canceled;
  issues marked as duplicates move by themselves into a reserved Duplicate status. Teams can add
  and rename statuses provided each category keeps at least one, and can reorder statuses within
  a category, while the categories keep a fixed order. Linear's own example puts Canceled, Could
  not reproduce and Won't Fix in the Canceled category and Duplicate in its own
  [doc: ISSUE-PROPERTY]. The Duplicate status is managed by the system and can be neither
  renamed nor customised. [doc: ISSUE-PROPERTY]
- Triage is a further status category [doc: ISSUE-PROPERTY]. Auto-close (paraphrased): an
  auto-closed issue is given one of the Closed statuses [doc: DELETE-ARCHIVE]; which type
  (`canceled` presumably) is **[not stated]**; `autoClosedAt` marks it [schema].
- Mapping a state's `name` to its `type` needs `state { name type }` per issue (or
  `workflowStates`, the statuses of the teams the caller can reach) [schema].

### E.3 State history and change history

- `stateHistory` → `IssueStateSpan { id, stateId, state, startedAt, endedAt }` (paraphrased):
  `startedAt` is the span's inclusive start; `endedAt` its exclusive end, null for the span still
  open; `state` is null when the status no longer exists. [schema:
  IssueStateSpan]
- `history` → `IssueHistory` entries (paraphrased): changes close in time can be grouped into one
  entry, and an entry is not a full snapshot of the issue; `fromStateId`, `toStateId`, `actor`
  (null for some automated or integration changes, or when the user was deleted), `botActor`,
  `archived`, `trashed`, `autoClosed`, `updatedDescription` (it holds neither the old nor the
  new Markdown body) [schema: IssueHistory].

### E.4 Labels, team, project

- `IssueLabel`: `id`, `name`, `color`, `description`, `isGroup` (a group cannot be put on an issue
  directly), `parent` (the label's group, null when it is in none), `team` (null for a workspace
  label open to every team), `inheritedFrom`,
  `archivedAt` [schema: IssueLabel] (paraphrased). Archiving a label leaves it on the issues that
  have it; deleting a label takes it off them (paraphrased) [doc: ISSUE-PROPERTY].
  Whether `Issue.labels` returns archived labels without `includeArchived` is **[not stated]**.
- `Team`: `id`, `key` (the prefix of issue identifiers, `ENG` in `ENG-123`, and part of URLs),
  `name`, `displayName` (the team's name with its parent team's name in front when it has one),
  `visibility` (`public`, `private`, `restricted`), `parent`, `retiredAt`, `archivedAt`
  [schema: Team, TeamVisibility] (paraphrased). `private: Boolean!` is deprecated for `visibility` [schema].
- `Project`: `id`, `name`, `slugId`, `url`, `description` (short plain text), `content` (the
  whole project overview in Markdown), `status: ProjectStatus` (type `backlog`, `planned`,
  `started`, `completed`, `canceled`; the `paused` value is deprecated), `completedAt`,
  `canceledAt`, `lead`, `teams` (private teams the caller cannot reach are left out), `trashed`
  (a trashed project is archived as well), `projectUpdates` [schema: Project, ProjectStatus]
  (paraphrased).
- Issues can be in one team only, projects can span teams: a project can belong to one team or be
  shared by many, while an issue belongs to exactly one team (paraphrased) [doc: TEAMS].

## F. Archived, trashed, deleted, retired

- Archive is automatic only (paraphrased): archiving happens by itself, and items cannot be
  archived by hand. Period set in Team Settings > Automations; a change usually applies at the
  next auto-archive run, as a rule within 24 hours.
  A closed issue is auto-archived once it has stayed completed, canceled or auto-closed, with no
  activity, for the whole auto-archive period (paraphrased). [doc: DELETE-ARCHIVE] The selectable periods are **[not stated]** in the pages read.
- Listing (paraphrased): paginated responses hide archived resources unless the optional
  `includeArchived: true` is passed with the pagination arguments.
  [doc: GRAPHQL] `issue(id:)` returns archived issues [schema: Query.issue].
- Delete = trash for 30 days (paraphrased): `issueDelete` moves an issue to the trash, along with
  the unarchived sub-issues the caller can reach, and a trashed issue can be restored within the
  30-day grace period [schema: Mutation.issueDelete]. Recently deleted issues are kept in the
  archive for 30 days and then removed from the workspace for good (paraphrased).
  [doc: DELETE-ARCHIVE]
- **CONFLICT** (unresolved, matters for listing; paraphrased): `includeArchived` leaves out deleted
  resources [schema: Query.issues] versus a trashed issue being archived as well
  [schema: Issue.trashed]. Whether `issues(includeArchived: true)` returns trashed issues (with
  `trashed: true`), and what an issue looks like after permanent deletion (gone; `issue(id:)`
  fails), is **[not stated]**. QUESTION.
- A trashed issue's `archivedAt`, and whether trashing moves `updatedAt`: **[not stated]**.
- Deleting a team deletes its issues for good too, after a 30-day grace period (paraphrased)
  [doc: TEAMS]. Retired teams (paraphrased): their issues can be read but not changed, and they
  keep following Linear's archive rules [doc: TEAMS]; `Team.retiredAt` and
  `TeamFilter.retiredAt` exist [schema].
- Workspace deletion: scheduled 48 hours after confirmation, after which the workspace and all
  its data are deleted for good (paraphrased) [doc: WORKSPACES].

## G. Comments

### G.1 Reading

- `Issue.comments(...)` with the standard connection arguments (no `sort`); default order
  `createdAt`, newest first with `first` and oldest first with `last` (paraphrased)
  [schema: Issue.comments]. So reading a thread oldest-first means paging with `last`/`before`
  or reversing pages read with `first`. Whether `last: N` without `before` returns the N oldest
  comments is not spelled out beyond that sentence: QUESTION.
- `Query.comments(filter: { issue: { id: { eq: ... } } })` also exists (every comment the user can
  reach in the workspace, paraphrased) [schema: Query.comments, CommentFilter].
- `CommentFilter.parent`: `null: true` gives root comments, `null: false` gives replies
  (paraphrased). [schema]

### G.2 Fields [schema: Comment]

| Field | Type | Description (paraphrased, trimmed) |
|---|---|---|
| `id` | ID! | stable UUID |
| `body` | String! | Paraphrased: the comment's content in Markdown. |
| `createdAt`, `updatedAt` | DateTime! | as on Issue |
| `editedAt` | DateTime | Paraphrased: when the author last edited the comment; null when it was never edited. |
| `user` | User | Paraphrased: the Linear user who wrote the comment; null when none is recorded or the account was deleted. |
| `botActor` | ActorBot | Paraphrased: the bot the comment is credited to; null when a Linear user is credited. |
| `externalUser` | ExternalUser | Paraphrased: the external user who wrote the comment through an integration such as Slack or Intercom; null for comments by workspace users. |
| `onBehalfOf` | User | Paraphrased: the user the comment was made for, as when an agent posts for a user; kept apart from the comment's author. |
| `parent` / `parentId` | Comment / String | Paraphrased: the root comment of the reply's thread; null for a root comment. |
| `children` | CommentConnection! | Paraphrased: replies point straight at the root and do not nest under other replies. |
| `quotedText` | String | Paraphrased: the text the comment refers to, for inline comments on documents or issue descriptions. |
| `resolvedAt`, `resolvingComment`, `resolvingUser` | | thread resolution, root only |
| `issue` / `issueId` | Issue / String | null when the comment belongs to another parent type |
| `url` | String! | Paraphrased: the address that opens the comment in Linear, with its comment anchor. |
| `archivedAt` | DateTime | as on Issue |
| `agentSession`, `externalThread`, `syncedWith`, `reactionData`, `reactions` | | other |

- Threads (paraphrased): a thread is a root comment and the replies whose parent is that root;
  resolution belongs to the root, and replies make no further nested threads.
  [schema: Comment]
- Inline comments on the description are included in `Issue.comments` [schema: Issue.comments],
  but the filter `IssueFilter.comments` excludes them: inline comments on the description are
  linked through `documentContent` and this filter does not cover them (paraphrased). [schema:
  IssueFilter.comments]
- Edits: `editedAt` (above). The webhook comment example carries `"edited": false`
  [example: WEBHOOKS]; `Comment` in SCHEMA has no `edited` field. Edit history of a comment's
  text: **[not stated]** (no field for it).
- Integration and bot comments: identified by `botActor` (`ActorBot.type` is the source category,
  such as `integration`, `oauthClient`, `workflow` or `ai`; `subType` the integration service, such
  as `github` or `slack`; `name`; `userDisplayName` the name of the external user who set the
  action off; `id` is not to be taken for a Linear User UUID) or `externalUser` [schema: ActorBot,
  Comment] (paraphrased). Built-in AI and workflow actions may act as the shared Linear actor
  instead of a user account of their own (paraphrased). [schema: ActorBot] An app installed with
  `actor=app` is itself a `User` with `app: true`
  [schema: User.app; doc: OAUTH-ACTOR].
- System comments (status changes, auto-close notices) as `Comment` rows: **[not stated]**; status
  changes are recorded in `history` / `stateHistory` (E.3), and auto-close puts a history item in
  the issue's Activity feed (paraphrased) [doc: DELETE-ARCHIVE]. AI summaries of resolved threads exist in the
  product [doc: COMMENTS]; whether they are exposed as comments is **[not stated]**.
- Deleted comments: they can be deleted in the app [doc: COMMENTS]; how a deleted comment appears
  (absent, archived, trashed) is **[not stated]**.

## H. Body formats, mentions, files

- Issue `description`, comment `body`, project `content` and project-update `body` are Markdown
  [schema: Issue.description, Comment.body, Project.content, ProjectUpdate.body]. The text editor
  supports most Markdown elements (paraphrased). [doc: EDITOR] Collapsible sections use `+++ Section title` ... `+++`
  [doc: GRAPHQL]; the editor also makes Mermaid diagrams, tables, checklists and date mentions
  [doc: EDITOR]. Which Markdown dialect, and how tables, checklists, dates and collapsibles are
  serialised when read: **[not stated]**.
- Mentions, as written through the API: a mention is made in Markdown with the resource's plain
  URL (paraphrased), e.g. `https://linear.app/yourworkspaceurl/profiles/someuser` and
  `https://linear.app/yourworkspaceurl/issue/LIN-123/some-issue` [example: GRAPHQL]. How a mention
  made in the app reads back in `description` / `body` (plain URL, `@name`, or other) is
  **[not stated]**. QUESTION. A suspended user's profile is at
  `linear.app/<workspace-name>/profiles/<username>` [doc: MEMBERS]; whether `<username>` is
  `displayName` is **[not stated]**.
- Uploaded files and images (paraphrased): images and other files uploaded to Linear are served
  behind authentication, to signed-in users only; an application that shows them outside Linear
  should download them and host them itself. [doc: GRAPHQL] The files are served from the
  `https://uploads.linear.app` host. Fetch them with the same `Authorization` header as the API [doc: FILES].
- Signed URLs (paraphrased): a request can ask that every file-storage URL in the response carry
  a signature giving temporary access, by sending the header `public-file-urls-expire-in` with the
  expiry in seconds as an integer. [doc: FILES] The maximum value, and how long an unsigned upload URL keeps
  working (for example after the issue is deleted), are **[not stated]**. That image links inside
  Markdown are `uploads.linear.app` URLs is implied (FILES speaks of files uploaded to Linear,
  images and attachments among them) but their exact Markdown form is **[not stated]**.
- "Issue attachments" in the API are something else: links to external resources (GitHub pull
  requests are the page's comparison) with `url`, `title`, `subtitle`, `metadata`, `sourceType`
  [doc: ATTACHMENTS] [schema: Attachment]. CSV exports leave attachment files out (paraphrased).
  [doc: EXPORT]

## I. Users, names and emails

- `User` fields that matter [schema: User] (paraphrased): `id`, `name` (the full name),
  `displayName` (the display or nick name, unique in the workspace), `email` (`String!`, the
  email address), `active` (false for suspended or otherwise disabled accounts; inactive users
  can still show on past work), `app` (whether the account is an installed application and not
  a person), `guest`, `url` (the profile URL), `avatarUrl`, `archivedAt`.
- In the app, people choose to see full names or usernames [doc: PROFILE] (PREFS: full names or
  the username each person chose) [doc: PREFS] (paraphrased); the API field
  for "username" is presumably `displayName` (**[not stated]**).
- `ExternalUser` (Slack, Jira, GitHub and the like): `displayName` (not unique), `name`, `email`
  (lowercase, null when unknown) [schema: ExternalUser] (paraphrased).
- Where an email can reach the connector:
  - `User.email` and `ExternalUser.email`, only if selected (GraphQL).
  - Webhook payload `actor` objects: `"email": "orbit@linear.app"` [example: WEBHOOKS] (not used
    by this connector).
  - `reactionData`: the count and the reacting users for each emoji (paraphrased) [schema:
    Issue.reactionData]; which user fields are included is **[not stated]**. Do not select it.
  - `Attachment.metadata` from integrations (ticket messages and named attributes, paraphrased)
    [schema]: contents **[not stated]**.
  - Free text in titles, descriptions and comments.
- Deleted or suspended people (paraphrased): suspended users lose all access at once and stay
  listed among the members for the record [doc: MEMBERS]; `creator` / `Comment.user` become
  null when the account was deleted [schema].
- Listing users: `users(...)` leaves disabled accounts out unless `includeDisabled` is true
  (paraphrased). [schema: Query.users]

## J. Project updates

- `projectUpdates(filter: ProjectUpdateFilter, first, after, includeArchived, orderBy, ...)`:
  every project status update in the workspace, optionally filtered (paraphrased). [schema:
  Query.projectUpdates] Filter fields: `project`, `createdAt`, `updatedAt`, `user`, `id`,
  `reactions` [schema: ProjectUpdateFilter]. Also `Project.projectUpdates(...)` (the project's
  published status updates; no filter argument) [schema: Project].
- `ProjectUpdate` [schema] (paraphrased): `id`, `body` (the update in Markdown), `health`
  (`onTrack`, `atRisk`, `offTrack`), `user: User!` (who wrote it),
  `createdAt`, `updatedAt`, `editedAt` (when it was edited), `url`, `slugId`,
  `project: Project!`, `diffMarkdown` (what changed in the project's details and progress since
  the previous update, in Markdown), `shortSummary` (a short summary made by AI), `isStale`,
  `comments`, `archivedAt`.
- Only the creator can edit an update; updates can be deleted [doc: UPDATES]. How a deleted
  update appears: **[not stated]**.
- Private-team-only projects: a project must be associated with at least one public team for
  its updates to be posted where the workspace-level project settings choose [doc:
  UPDATES] (a Slack statement; API visibility of updates on private projects follows project
  visibility, section K, by inference only.)

## K. Keys, permissions, private teams, what an admin controls

### K.1 Personal API key

- Where (paraphrased): Admins, and Members who are allowed to, make personal API keys under
  _Settings > Account > Security & Access_. Each key either gets all the data its user can reach
  or is held to chosen permissions (_Read_, _Write_, _Admin_, _Create issues_, _Create
  comments_), and a key can also be limited to chosen teams of the workspace.
  [doc: API-DOCS] SEC-ACCESS: a person can make API keys for their own account with chosen
  permissions, optionally limited to some teams (paraphrased). [doc: SEC-ACCESS]
- So a key can be read-only (Read) and team-limited. What a team-limited key gets when it asks for
  another team's issues (empty list, error, or partial `errors`): **[not stated]**. QUESTION.
- Who may create one (paraphrased): Admins decide whether Members can make their own API keys,
  under _Settings > Administration > API > Member API keys_; the setting does not bind Admins,
  who can always make keys. [doc: API-DOCS] SCHEMA also has `personalApiKeysRole`, the lowest
  role allowed to make personal API keys (paraphrased). [schema: OrganizationSecuritySettingsInput] On
  Enterprise, workspace owners can set which roles may take certain workspace-level actions
  (paraphrased) [doc: MEMBERS]. On Free plans every user is an Admin [doc: MEMBERS]. Guests: **[not
  stated]**.
- Admin revocation: the workspace's existing API keys are listed in the same menu and can be
  revoked there (paraphrased). [doc: API-DOCS]
- Key lifetime or expiry, key prefix and format, and whether suspending the owner disables the key
  (implied by MEMBERS saying that suspended users lose all access at once [doc: MEMBERS], not said of keys):
  **[not stated]**.
- What the key sees: the owner's access, narrowed by the key's permissions and teams. The key acts
  as the user (by default every Linear API authentication method takes the authenticated user as
  the API actor, paraphrased [doc: OAUTH-ACTOR]).
- Storage region: API keys made by users are among the data always stored in the United States
  (paraphrased) [doc: SECURITY].

### K.2 Private teams and guests

- The API can read private issue data through the personal API key of a user who has access to
  private teams (paraphrased). [doc: PRIVATE-TEAMS]
- People who are not members of a private team cannot see that team's issues (paraphrased).
  [doc: PRIVATE-TEAMS] On Enterprise, workspace owners reach a private team's issues only once
  they join the team; on other paid plans admins can see private teams in settings
  and join them [doc: PRIVATE-TEAMS] (paraphrased). So an admin's key does not by itself read private teams it
  has not joined (stated for the app; the API is not singled out).
- Projects shared across public and private teams: the project's issues tied to the private team
  are visible only to that team's members (paraphrased). [doc: PRIVATE-TEAMS] A project-scoped listing by a
  non-member therefore omits those issues; `Project.teams` omits private teams the caller cannot
  reach [schema].
- Enterprise issue sharing lets a private issue be shared with non-members, who can neither see
  nor change its team, project, cycle or project milestone (paraphrased) [doc: PRIVATE-TEAMS]. What `issue.team`
  and `issue.project` return through such a user's key is **[not stated]**.
- When a team becomes private, non-members are taken off the issues they were assigned to
  (paraphrased) [doc: PRIVATE-TEAMS].
- Guests reach issues, projects and documents of the teams they were added to by name
  (paraphrased) [doc: MEMBERS].
- Sub-teams: a private parent's sub-teams are `restricted` (visible to parent members) or `private`
  [doc: SUB-TEAMS, PRIVATE-TEAMS]; `Team.issues` includes sub-team issues only with
  `includeSubTeams: true` [schema].

### K.3 What an OAuth application or a workspace-level credential would change (brief)

- OAuth app (authorization code): token `Authorization: Bearer`; the access token lasts 24 hours,
  the refresh token 30 days from issue and is replaced on each refresh, and API requests lengthen
  neither (paraphrased). [doc: OAUTH] Scopes: `read` (the default, read access to the user's
  account, always present), `write`, `issues:create`, `comments:create`,
  `timeSchedule:write`, `admin` [doc: OAUTH]. Higher request limit (5,000 per hour per user or app
  user) but lower complexity (2,000,000) [doc: RATE-LIMITS]. Workspaces on paid plans can require
  approval of third-party apps [doc: APP-APPROVALS]; users see and revoke authorized apps under
  Security & access [doc: SEC-ACCESS].
- A workspace-level read credential: the documented route is an OAuth app with the
  client-credentials grant, an `app` actor token that reaches every public team in the workspace
  and lasts 30 days, no refresh token, renewed by asking again; the app user's team access can be
  changed on the app's details page (paraphrased) [doc: OAUTH]. It must be switched on
  for the app, and rotating the client secret ends every client-credentials token at once
  (paraphrased) [doc: OAUTH]. Workspace-level OAuth apps with actor authorization get dynamically higher
  limits [doc: RATE-LIMITS].
- `AccessKey` in SCHEMA is a key for CI/CD integrations; unlike a personal API key it is not
  bound to one user's permissions but to an organization, with set capabilities and resource
  access (release pipelines, for example) (paraphrased). [schema:
  AccessKey] Whether an access key can read issues is **[not stated]**; no docs page describes it.
- Third-party app approvals do not mention personal API keys [doc: APP-APPROVALS].

## L. Webhooks (only for what they say about change semantics)

- Events are `create`, `update` or `remove`; `updatedFrom` holds the earlier values of every
  property that changed (paraphrased) [doc: WEBHOOKS]. Models with data-change webhooks include `Issues`,
  `Issue comments`, `Issue labels`, `Projects`, `Project updates`, `Users` [doc: WEBHOOKS].
- Webhooks belong to an `Organization` and can be set to report on all public teams or on one
  team; only workspace admins, or OAuth applications with the `admin` scope, can create or read
  them (paraphrased). [doc: WEBHOOKS]
- Delivery: retried at most 3 times, after 1 minute, 1 hour and 6 hours [doc: WEBHOOKS].
- `actor` (paraphrased): a `User`, an OAuth client or an `Integration`; it can be null when the
  user or integration that set the action off has been deleted since. [doc: WEBHOOKS]
- Whether trashing an issue sends `remove` or an `update` with `trashed`, and whether archiving
  sends an event, is **[not stated]**.
- Quotas: 30 webhooks per team and 50 per workspace [doc: CHANGELOG, October 8, 2026 entry].

## M. Terms and policy (in our words, not interpreted)

**Product and developer pages**
- Customers own their data in Linear, and the GraphQL API lets them query it (paraphrased).
  [doc: API-DOCS]
- A workspace's data can be exported to build custom reports, keep records or feed LLMs
  (paraphrased). [doc: EXPORT] A section heading of EXPORT is about copying issues as Markdown for
  LLMs [doc: EXPORT].
- The API, webhooks and integrations such as Zapier can expose the titles of a user's private
  teams and issues (paraphrased). [doc: PRIVATE-TEAMS]
- Polling: see the statements at the end of section C [doc: RATE-LIMITS, GRAPHQL].
- Customers answer for how they use Linear, which covers setting the workspace's security and
  access, deciding what data they store and keep, managing API keys and integrations, and
  watching their audit log (paraphrased). [doc: SECURITY]

**Terms of Service** (Effective June 09, 2026) [doc: TERMS], paraphrased:
- 1.2: only individuals the Customer authorizes may use and access the Service, and only for the
  Customer's own internal business purposes, not for any third party's benefit.
- 2.2 (Use Restrictions) includes (g): no use of or access to the Service to build or support,
  or help a third party build or support, products or services that compete with the Service.
- 2.3 API Access Restrictions: Linear may, at its sole discretion, set and enforce limits on the
  Customer's use of the API, which the Customer agrees to keep to; Linear may also suspend the
  Customer's API access or stop providing the API at any time.
- 10.1: the Customer bears all resulting liability if usernames, passwords, tokens or keys it
  holds are used by anyone not authorized to use them.
- 10.2: apart from the limited rights and licences the Agreement grants, the Customer owns all
  right, title and interest in the User Submissions.
- 10.5 AI Services concerns the artificial intelligence or machine learning features offered
  through the Service, governed by the AI Addendum.

**Acceptable Use Policy** (Effective June 09, 2026) [doc: AUP], paraphrased:
- Among the uses of the Services a user may neither make nor let others make: (d) reaching the
  Services by automated means beyond the rate limits and parameters in Linear's developer
  Documentation; (e) scraping or extracting data other than through Linear's published APIs.
- AI-specific restrictions apply to the AI features of the Services (Linear's own AI features),
  for example: inputs to or outputs of the AI Features may not be used to train, fine-tune,
  distill or extract weights from an AI model that competes with the AI Features or with the
  models under them.
- Data under specific legal protections beyond general privacy or data protection law may not be
  submitted to the Services (HIPAA PHI, PCI, and others listed).

**AI Services Addendum** (Effective June 09, 2026) [doc: AI-ADDENDUM], paraphrased:
- Scope, 1.2: "AI Service" means any artificial intelligence Service, feature or related
  functionality that Linear provides to the Customer as part of the Service.
- 13.2: Linear will not use Customer Data, Prompts and Outputs included, to train, fine-tune or
  otherwise improve its own AI models.
- Nothing found in the addendum, the Terms, the AUP or the developer pages about a customer
  sending data read through the API to the customer's own or a third party's AI models:
  **[not stated]** (keyword search; see the header for what was read in full).

---

## Open questions for the connector

Most important first.

1. QUESTION: Ordering for a listing of issues finished since a time T. `orderBy` offers only `createdAt` / `updatedAt`,
   newest first with `first`; `sort` has `completedAt` (not `canceledAt`) and cannot be combined
   with `orderBy`. Is a `sort`-ordered listing stable across pages with `after`, what is the
   tie-break for equal timestamps, and is the cursor a snapshot or a live position? (D.2, D.4)
2. QUESTION: Are trashed (deleted) issues returned by `issues(includeArchived: true)`? The schema
   says both that deleted resources are left out and that trashed issues are archived as well
   (paraphrased) (F).
   And what does an issue look like once permanently deleted after 30 days (only `issue(id:)`
   failing)?
3. QUESTION: The exact error envelope: does a real error carry `extensions.code` (docs example),
   `extensions.type` (what the SDK reads), or both, and with which values and HTTP statuses for a
   missing, malformed or revoked API key, a key without access to a team, a missing issue, a
   query over 10,000 points, and a GraphQL validation error? (B)
4. QUESTION: After a reopen and a second completion, is `completedAt` the latest entry into a
   completed status? Does it change on a move between two completed states, or `canceledAt`
   between two canceled ones? (E.1) `stateHistory` can answer it per issue at extra cost.
5. QUESTION: What does a team-limited personal key get for other teams: an empty list, an error,
   or partial `errors` with `data`? And for a project whose issues span teams outside the key's
   teams? (K.1, K.2)
6. QUESTION: How do mentions and uploaded images appear inside `description` and comment `body`
   when read (plain URLs, `@name`, Linear-specific markup), and how long does an
   `uploads.linear.app` URL (signed or not) stay fetchable? (H)
7. QUESTION: Does `last: N` on `Issue.comments` return the N oldest comments oldest-first, so that
   a thread can be read in order without collecting every page first? (G.1)
8. QUESTION: Do personal API keys expire, and do they stop working when their owner is suspended
   or removed from a private team? What is the key's format (prefix, length) for config
   validation? (K.1)
9. QUESTION: Is the auto-archive period short enough in practice to archive a finished issue before
   a scheduled read (selectable periods are not in the pages read), so that `includeArchived:
   true` is needed on every listing? (F)
10. QUESTION: Are system or AI-generated messages (auto-close notices, resolved-thread summaries,
    agent activity) ever returned as `Comment` rows, and how are they marked beyond `botActor`?
    (G.2)
11. QUESTION: How do deleted comments and deleted project updates appear (absent, archived,
    trashed)? (G.2, J)
12. QUESTION: Does the schema file in the SDK repository match what the live API serves (no
    introspection was run)? Fields marked `[ALPHA]` (for example `documentContent`) may change.
