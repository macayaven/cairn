# GitHub REST API shapes for a one-repository connector (merged PRs, closed issues, releases)

Researched 2026-11-14. No GitHub API was called, nothing was signed in to, no account was
opened. Every docs page below was fetched that day with `curl` (User-Agent `curl/8`) twice: as
HTML (`https://docs.github.com<path>`, converted with `h2t.py`) and as Markdown through the
docs site's article endpoint `https://docs.github.com/api/article/body?pathname=<path>` (same
text, with each endpoint's parameter and response-schema lists rendered as Markdown). Every
request answered 200; no page failed. No docs.github.com page shows a "Last updated" line; the
two site-policy pages show "Effective date: April 27, 2026". The connector and its fake are to
be built from these notes, not from memory.

The OpenAPI description (GitHub's equivalent of a discovery document) was read from the
`github/rest-api-description` repository at commit
`2a33d9cc5d1dc6ca603b908e0796454af88c1999` (the head of `main` on 2026-11-14, found with
`git ls-remote`; the newest tag is `v2.1.0`), fetched from raw.githubusercontent.com:

- **SPEC** = `descriptions/api.github.com/api.github.com.2026-03-10.json` (OpenAPI 3.0.3,
  `info.version` `1.1.4`, server `https://api.github.com`). Every **[spec]** tag means this file
  unless it says otherwise.
- **SPEC-2022** = `descriptions/api.github.com/api.github.com.2022-11-28.json` (same
  `info.version`), compared with SPEC where the two API versions differ.
- The unversioned `api.github.com.json` was fetched and is **not** identical to SPEC-2022
  (paths and components differ); it was not used.
- **SPEC-README** = the repository README. It says: "Not all headers are described in the
  OpenAPI documents, expect those to be added over time."

Docs pages (final URL = the requested URL under `https://docs.github.com` unless shown):

- **GETTING-STARTED** = /en/rest/using-the-rest-api/getting-started-with-the-rest-api
- **API-VERSIONS** = /en/rest/about-the-rest-api/api-versions
- **BREAKING** = /en/rest/about-the-rest-api/breaking-changes
- **ABOUT-OPENAPI** = /en/rest/about-the-rest-api/about-the-openapi-description-for-the-rest-api
- **PAGINATION** = /en/rest/using-the-rest-api/using-pagination-in-the-rest-api
- **RATE-LIMITS** = /en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- **BEST-PRACTICES** = /en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- **TROUBLESHOOTING** = /en/rest/using-the-rest-api/troubleshooting-the-rest-api
- **TIMEZONES** = /en/rest/using-the-rest-api/timezones-and-the-rest-api
- **EVENT-TYPES** = /en/rest/using-the-rest-api/issue-event-types (scanned for the event types
  named in section E.5; its availability tables are drawn with icons, read from the HTML's
  `aria-label` "Supported"/"Not supported")
- **AUTH-REST** = /en/rest/authentication/authenticating-to-the-rest-api
- **FG-PERMISSIONS** = /en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
  (scanned: intro, Metadata, and the rows for the endpoints below)
- **FG-ENDPOINTS** = /en/rest/authentication/endpoints-available-for-fine-grained-personal-access-tokens
  (fetched, not read; each endpoint page states its own permissions)
- **PULLS** = /en/rest/pulls/pulls (sections List, Get, Check if merged)
- **REVIEWS** = /en/rest/pulls/reviews (intro, List reviews)
- **REVIEW-COMMENTS** = /en/rest/pulls/comments (intro, both List sections)
- **ISSUES** = /en/rest/issues/issues (intro, List repository issues, Get an issue)
- **ISSUE-COMMENTS** = /en/rest/issues/comments (intro, both List sections)
- **ISSUE-EVENTS** = /en/rest/issues/events (intro, List for a repository, List for an issue)
- **TIMELINE** = /en/rest/issues/timeline
- **RELEASES** = /en/rest/releases/releases (intro, List, Get latest, Get a release)
- **SEARCH** = /en/rest/search/search (About search, Search issues and pull requests)
- **RATE-LIMIT-EP** = /en/rest/rate-limit/rate-limit
- **SEARCH-SYNTAX** = /en/search-github/getting-started-with-searching-on-github/understanding-the-search-syntax
- **SEARCH-ISSUES** = /en/search-github/searching-on-github/searching-issues-and-pull-requests
  (scanned: date, closed, merged, `is:`, `reason:` and archived qualifiers)
- **PAT-MANAGE** = /en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
- **TOKEN-EXPIRY** = /en/authentication/keeping-your-account-and-data-secure/token-expiration-and-revocation
- **PAT-ORG-POLICY** = /en/organizations/managing-programmatic-access-to-your-organization/setting-a-personal-access-token-policy-for-your-organization
- **PAT-ORG-REQUESTS** = /en/organizations/managing-programmatic-access-to-your-organization/managing-requests-for-personal-access-tokens-in-your-organization
- **SAML-PAT** = requested /en/authentication/authenticating-with-single-sign-on/authorizing-a-personal-access-token-for-use-with-single-sign-on;
  final URL /en/enterprise-cloud@latest/authentication/authenticating-with-single-sign-on/authorizing-a-personal-access-token-for-use-with-single-sign-on
- **IP-ALLOW** = requested /en/organizations/keeping-your-organization-secure/managing-security-settings-for-your-organization/managing-allowed-ip-addresses-for-your-organization;
  final URL /en/enterprise-cloud@latest/organizations/keeping-your-organization-secure/managing-security-settings-for-your-organization/managing-allowed-ip-addresses-for-your-organization
  (scanned: intro and the list of what the allow list protects)
- **APP-INSTALL-AUTH** = /en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation
- **OAUTH-SCOPES** = /en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps (scope table only)
- **TRANSFER-ISSUE** = /en/issues/tracking-your-work-with-issues/administering-issues/transferring-an-issue-to-another-repository
- **DELETE-ISSUE** = /en/issues/tracking-your-work-with-issues/administering-issues/deleting-an-issue
- **CLOSE-ISSUE** = /en/issues/tracking-your-work-with-issues/administering-issues/closing-an-issue
- **MODERATE-DISCUSSIONS** = /en/discussions/managing-discussions-for-your-community/moderating-discussions
- **LOCK** = /en/communities/moderating-comments-and-conversations/locking-conversations
- **MANAGE-COMMENTS** = /en/communities/moderating-comments-and-conversations/managing-disruptive-comments
- **ARCHIVE-PRS** = /en/communities/moderating-comments-and-conversations/archive-pull-requests
- **MANAGE-RELEASES** = /en/repositories/releasing-projects-on-github/managing-releases-in-a-repository
- **ARCHIVING** = /en/repositories/archiving-a-github-repository/archiving-repositories
- **REPO-VISIBILITY** = /en/repositories/creating-and-managing-repositories/about-repositories
- **ATTACHING-FILES** = /en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files
- **ANON-URLS** = /en/authentication/keeping-your-account-and-data-secure/about-anonymized-urls
- **DELETE-ACCOUNT** = requested /en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-your-personal-account/deleting-your-personal-account;
  final URL /en/account-and-profile/how-tos/account-management/deleting-your-personal-account
- **ACCOUNT-REF** = /en/account-and-profile/reference/personal-account-reference (side-effects sections)
- **TERMS** = /en/site-policy/github-terms/github-terms-of-service ("Effective date: April 27,
  2026"; sections A, C, D, E.1 to E.3, H and J read; the rest skimmed)
- **AUP** = /en/site-policy/acceptable-use-policies/github-acceptable-use-policies (sections 4 to 9 read)
- **PRIVACY** = /en/site-policy/privacy-policies/github-general-privacy-statement ("Effective
  date: April 27, 2026"; opening and third-party sections read, the rest keyword-searched)
- **GHES-GETTING-STARTED**, **GHES-RATE-LIMITS**, **GHES-API-VERSIONS** = the same three REST
  pages under /en/enterprise-server@latest/...; final URLs under /en/enterprise-server@3.22/...
  (GHES-GETTING-STARTED read only for the base URL; the other two in full)

Not fetched: the Users endpoint page (where display names come from), the GitHub App
permission reference, the Additional Product Terms, the GitHub
Customer Agreement, the ghe.com (data residency) docs, the webhooks docs, the GraphQL docs.

Tags: **[doc: X]** = stated in prose or in a rendered parameter/schema list on page X;
**[example: X]** = only visible in an example request or response on page X, not stated as a
rule; **[spec]** = a fact from SPEC (type, `format`, `nullable`, `required`, enum, default,
`maximum`), or **[spec-2022]** from SPEC-2022; **[not stated]** = nowhere in the pages or spec
files above. **CONFLICT** = two provider statements disagree, both quoted. **QUESTION** = a gap
that matters for the connector's design.

Paths below are relative to the base URL; `{owner}/{repo}` is the one repository.

---

## Headline points (read first)

1. **Without a version header you get the 2022 API, and 2026-03-10 removes fields.**
   "Requests without the `X-GitHub-Api-Version` header will default to use the `2022-11-28`
   version." [doc: API-VERSIONS] The 2026-03-10 version removes `merge_commit_sha` from pull
   requests, the single `assignee` from issues and pull requests, and the `rate` object from
   `/rate_limit`. [doc: BREAKING] Pin the header and build the fake for one version (A.3).
2. **No listing answers merged-after-T or closed-after-T directly.** The pull request list
   has no `since` and sorts by `created`, `updated`, `popularity` or `long-running`; the issue
   list's `since` means "last updated after". [doc: PULLS, ISSUES] Whether a merge or a close
   moves `updated_at` is **[not stated]**. Search has `merged:` and `closed:` qualifiers but
   returns at most 1,000 results per query and allows 30 search requests a minute. [doc:
   SEARCH] Section G lays out the four routes.
3. **Who merged a pull request is only on the single-PR GET.** The list item ("Pull Request
   Simple") has `merged_at` but no `merged_by` and no `merged` boolean; `GET
   /repos/{owner}/{repo}/pulls/{pull_number}` has both. [spec] So every merged PR costs at least
   one extra request (or the `actor` of its `merged` event, E.5).
4. **The issue list returns pull requests too, and its first sentence says it lists only open
   issues.** "List issues in a repository. Only open issues will be listed." yet `state` takes
   `open`, `closed` or `all`. [doc: ISSUES] **CONFLICT**. Pull requests are told apart by the
   `pull_request` key, and "the id of a pull request returned from "Issues" endpoints will be
   an issue id" (sic, nested quotes on the page). [doc: ISSUES] Under 2026-03-10 the
   `pull_request` property "with `null` default values" is deprecated (A.3), so test for a
   non-null `pull_request`, not for the key alone.
5. **In an organisation, a fine-grained token needs approval by default and reads only public
   data until approved.** "Require administrator approval" is "the default value" [doc:
   PAT-ORG-POLICY]; a pending token "will only be able to read public resources until it is
   approved". [doc: PAT-MANAGE] The connector cannot tell "approved" from "pending" except by
   failures on the private repository (404, B.6).
6. **A pull request's conversation comments are issue comments; review comments and review
   bodies are separate endpoints.** "For information about how to interact with comments on a
   pull request, see REST API endpoints for issue comments" (link text). [doc: PULLS] Review
   summaries come from the reviews list ("returns in chronological order"); line comments
   from the review comments list. [doc: REVIEWS, REVIEW-COMMENTS]
7. **Logins are not permanent identities.** On account deletion "Your username will be
   available for anyone to use after 90 days." [doc: ACCOUNT-REF] User objects carry a numeric
   `id` (int64) beside `login`, and `user` may be `null`. [spec]
8. **Search's own permission rule and its access rule pull in different directions.** "The
   fine-grained token does not require any permissions." but a `repo:` qualifier for a
   repository the caller cannot access fails with 422. [doc: SEARCH] Whether a fine-grained
   token with access to one private repository can search it is **[not stated]**.

---

## A. Requests in general

### A.1 Base URL and paths

- "the base URL for the GitHub REST API (`https://api.github.com`)" [doc: GETTING-STARTED];
  SPEC's only server is `https://api.github.com`. [spec]
- GitHub Enterprise Server: "`http(s)://HOSTNAME/api/v3`" [doc: GHES-GETTING-STARTED] (O.3).
- Trailing slash: "adding a trailing slash to the endpoint will result in a `404 Not Found`"
  [doc: TROUBLESHOOTING]. An unsupported method gives "a `404 Not Found` response instead of `405 Method Not
  Allowed`". [doc: TROUBLESHOOTING]
- Redirects: "You should assume that any request may result in a redirection." A `301` means
  the resource moved permanently (update the stored URL); `302` and `307` are temporary (keep
  using the original). [doc: BEST-PRACTICES] The transferred-issue case is a 301 (E.3).

### A.2 Request headers

- **Accept**: "Most GitHub REST API endpoints specify that you should pass an `Accept` header
  with a value of `application/vnd.github+json`." [doc: GETTING-STARTED] Custom media types
  have the form `application/vnd.github.PARAM+json` and select the body format (M.1).
  [doc: GETTING-STARTED]
- **X-GitHub-Api-Version**: A.3.
- **User-Agent**: "All API requests must include a valid `User-Agent` header." "Requests with
  no `User-Agent` header will be rejected." "If you provide an invalid `User-Agent` header, you
  will receive a `403 Forbidden` response." [doc: GETTING-STARTED] The status code for a
  *missing* User-Agent, and what makes one invalid, are **[not stated]**. "GitHub recommends using your GitHub username, or the name of your application, for the
  `User-Agent` header value. This allows GitHub to contact you if there are problems." [doc:
  GETTING-STARTED] Note for the connector: that advice asks for an identifying value; what
  name to send is a design choice.
- **Authorization**: "in most cases you can use `Authorization: Bearer` or `Authorization:
  token` to pass a token" [doc: AUTH-REST]. Username and password (Basic) authentication is
  not supported. [doc: AUTH-REST]
- **Time-Zone**: applies only to timestamps the caller sends, never to returned timestamps
  (A.5). [doc: TIMEZONES]
- **If-None-Match / If-Modified-Since**: conditional requests (I.3).

### A.3 Versions

- "The GitHub REST API is versioned. The API version name is based on the date when the API
  version was released." [doc: API-VERSIONS]
- Supported versions table [doc: API-VERSIONS]:

  | Version | End of support |
  |---|---|
  | `2026-03-10` | Not yet scheduled |
  | `2022-11-28` | March 10, 2028 |

- Default: "Requests without the `X-GitHub-Api-Version` header will default to use the
  `2022-11-28` version." [doc: API-VERSIONS]
- Retired version: "If you specify an API version that is no longer supported, you will
  receive a `410 Gone` response." [doc: API-VERSIONS] Non-existent version: a `400 Bad
  Request` (paraphrase). [doc: TROUBLESHOOTING]
- Before retirement, responses carry `Deprecation` (an HTTP date, RFC 7231) and `Sunset`
  ("The date when the API version will be completely removed (retired), after which requests
  will return a `410 Gone` response"). [doc: API-VERSIONS]
- After a version closes, "Requests that do not specify an API version default to the next
  oldest supported version, not the closing down version." [doc: API-VERSIONS]
- "When a new REST API version is released, the previous API version will be supported for at
  least 24 more months following the release of the new API version." [doc: API-VERSIONS]
- Exceptions: "Critical security vulnerabilities, data exposure risks, or severe reliability
  issues may require changes outside the normal release schedule." and "For certain services
  with very low usage, GitHub may deprecate functionality outside the standard versioning
  process." [doc: API-VERSIONS]
- Every curl example on the endpoint pages uses `X-GitHub-Api-Version: 2026-03-10`.
  [example: PULLS, ISSUES, RELEASES, SEARCH]
- Changes listed under "Version 2026-03-10" that touch this connector (headings quoted)
  [doc: BREAKING]:
  - "Remove `merge_commit_sha` field from pull request responses": "The `merge_commit_sha`
    property is removed from pull request payloads across all endpoints that return pull
    request objects."
  - "Remove deprecated singular "assignee" field from Issue and Pull Request endpoints"
    (inner quotes the page's): read the `assignees` array instead.
  - "Remove deprecated `rate` property from rate limit endpoint": read `resources.core`.
  - "Deprecate support for the `beta` media type": "there are still remnants of its use that
    modify response payloads. The following response properties are deprecated as a result:"
    among them "`pull_request` response property with `null` default values". The heading
    says **deprecate**, not remove, and the page does not say what replaces the property.
    Affected endpoints include `GET /repos/{owner}/{repo}/issues`, `/issues/{issue_number}`,
    `/issues/events`, `/issues/{issue_number}/timeline`, `GET /repos/{owner}/{repo}/pulls`,
    `/pulls/{pull_number}` and `GET /search/issues`. QUESTION: under 2026-03-10, does an
    issue that is not a pull request carry `pull_request: null` or no `pull_request` key, and
    does a pull request in the issue list still carry it? The headline 4 test (`pull_request`
    key present) depends on the answer; SPEC still defines `pull_request` on the issue object.
    [spec]
  - "Remove deprecated `has_downloads` property from Repository response".
- Spec difference that follows: in an issue object, `pull_request.merged_at`, `.url`,
  `.html_url`, `.diff_url`, `.patch_url` are `nullable: true` in SPEC-2022 and `nullable:
  false` in SPEC; `merged_at` is not in `pull_request.required` in either. [spec, spec-2022]
  QUESTION: under 2026-03-10, is `pull_request.merged_at` omitted (rather than `null`) for an
  unmerged pull request in the issue list?

### A.4 Response headers and body conventions

- Example response headers on the getting-started page include `ETag`, `Link`, `Location`,
  `Retry-After`, `X-RateLimit-*`, `X-OAuth-Scopes`, `X-Accepted-OAuth-Scopes`,
  `X-Poll-Interval`, `X-GitHub-Media-Type`, `X-GitHub-SSO`, `X-GitHub-Request-Id`,
  `Deprecation`, `Sunset` (in the CORS exposed-headers list). [example: GETTING-STARTED]
- "Blank fields are included as `null` instead of being omitted." "All timestamps return in UTC
  time, ISO 8601 format: `YYYY-MM-DDTHH:MM:SSZ`" [doc: GETTING-STARTED]. Compare the
  non-required, non-nullable fields in A.3: **CONFLICT** in practice for the fake (the prose
  says null, the spec allows absence).
- List endpoints return a "summary representation" with a subset of the fields; a single-item
  GET returns the "detailed representation" (paraphrase). [doc: GETTING-STARTED] For pull
  requests the difference is real (`merged_by`, `merged`, counts) [spec]; for issues the list
  and the GET share one schema [spec], but whether every field is filled in the list is
  **[not stated]**.
- Fields ending `_url` are hypermedia links; some are URI templates (paraphrase). [doc:
  GETTING-STARTED]
- Timeouts: "If GitHub takes more than 10 seconds to process an API request, GitHub will
  terminate the request and you will receive a timeout response and a "Server Error" message."
  [doc: TROUBLESHOOTING] Its status code is **[not stated]**.

### A.5 Time

- Returned timestamps: "Timestamps returned by the API are in UTC time, ISO 8601 format."
  [doc: TIMEZONES] Every timestamp property read in SPEC is `type: string, format: date-time`;
  examples are whole seconds with `Z` (`2011-01-26T19:01:12Z`). [spec] Sub-second precision is
  **[not stated]**.
- The `Time-Zone` header and the `+HH:MM` offsets in caller-supplied timestamps affect only
  input, for example timestamps sent when creating commits (paraphrase). [doc: TIMEZONES]
- `since` parameters take "a timestamp in ISO 8601 format: `YYYY-MM-DDTHH:MM:SSZ`". [doc:
  ISSUES, ISSUE-COMMENTS, REVIEW-COMMENTS]
- Search date qualifiers take `YYYY-MM-DD`, optionally with a time and offset (H.3).

---

## B. Authentication and the token

### B.1 Fine-grained personal access token (the first choice)

- Scope of one token: "Each token is limited to access resources owned by a single user or
  organization." and "Each token can be further limited to only access specific repositories
  for that user or organization." [doc: PAT-MANAGE] At creation the user picks a
  **Resource owner**: "The token will only be able to access resources owned by the selected
  resource owner." [doc: PAT-MANAGE]
- Public data always: "Tokens always include read-only access to all public repositories on
  GitHub." [doc: PAT-MANAGE]
- Expiry: "Infinite lifetimes are allowed but may be blocked by a maximum lifetime policy set
  by your organization or enterprise owner." [doc: PAT-MANAGE] The pre-fill URL parameter
  `expires_in` takes an "Integer between 1 and 366, or `none`" and "If not provided, the
  default is 30 days, or less if the target has a token lifetime policy set." [doc:
  PAT-MANAGE] Expiry date visible to the connector through the API: **[not stated]** (the
  pages read name no response header carrying it).
- Unused tokens: "GitHub automatically removes personal access tokens that haven't been used
  in a year." [doc: PAT-MANAGE] "GitHub will automatically revoke an OAuth token or personal
  access token when the token hasn't been used in one year." [doc: TOKEN-EXPIRY] A scheduled
  connector that runs at least yearly keeps it alive; an expired or revoked token "can no
  longer be used to authenticate Git and API requests" and cannot be restored. [doc:
  TOKEN-EXPIRY]
- Leaked tokens: "If a valid OAuth token, GitHub App token, or personal access token is pushed
  to a public repository or public gist, the token will be automatically revoked." [doc:
  TOKEN-EXPIRY] Prefixes listed for revocation: classic `ghp_`, fine-grained `github_pat_`,
  OAuth `gho_`, App user-to-server `ghu_`, App refresh `ghr_`. [doc: TOKEN-EXPIRY] Installation
  tokens' prefix is **[not stated]** there.
- Limit: "There is a limit of 50 fine-grained personal access tokens you can create." [doc:
  PAT-MANAGE]
- Known gaps (quoted from the list of major gaps) [doc: PAT-MANAGE]: "Using fine-grained
  personal access token to contribute to repositories where the user is an outside or
  repository collaborator." and "Using fine-grained personal access token to access multiple
  organizations at once." Also "Outside collaborators can only use personal access tokens
  (classic) to access organization repositories that they are a collaborator on." So if the
  token's owner is an outside collaborator on the repository, a fine-grained token does not
  work and a classic one is needed. [doc: PAT-MANAGE]
- Permissions this connector needs (all **read**; repository permissions) [doc: each endpoint
  page; FG-PERMISSIONS]:

  | Endpoint | Fine-grained permission |
  |---|---|
  | `GET /repos/{o}/{r}/pulls` | Pull requests |
  | `GET /repos/{o}/{r}/pulls/{n}` | Pull requests or Contents ("at least one of") |
  | `GET /repos/{o}/{r}/pulls/{n}/merge` | Pull requests |
  | `GET /repos/{o}/{r}/pulls/{n}/reviews` | Pull requests |
  | `GET /repos/{o}/{r}/pulls/{n}/comments`, `/pulls/comments` | Pull requests |
  | `GET /repos/{o}/{r}/issues`, `/issues/{n}` | Issues |
  | `GET /repos/{o}/{r}/issues/events` | Issues |
  | `GET /repos/{o}/{r}/issues/{n}/comments`, `/issues/comments` | Issues or Pull requests |
  | `GET /repos/{o}/{r}/issues/{n}/events`, `/timeline` | Issues or Pull requests |
  | `GET /repos/{o}/{r}/releases`, `/releases/{id}` | Contents |
  | `GET /repos/{o}/{r}` | Metadata |
  | `GET /search/issues` | none ("does not require any permissions") |

  FG-PERMISSIONS marks the multi-permission rows with a check in "Additional Permissions";
  each endpoint page states the alternatives. "These permissions are required to access
  private resources. Some endpoints can also be used to access public resources without these
  permissions." [doc: FG-PERMISSIONS] Whether **Metadata (read)** is added automatically to
  every fine-grained token is **[not stated]** as a rule; the pre-fill example says a URL with
  `contents=read` creates "a token with `contents:read` and `metadata:read`". [example:
  PAT-MANAGE]
- QUESTION: does the issue list return pull requests (with their `pull_request` key) to a token
  that has **Issues** but not **Pull requests**? Not stated. The connector should grant both.

### B.2 Header that names missing permissions

- "You can use the `X-Accepted-GitHub-Permissions` header to identify the permissions that are
  required to access the REST API endpoint." The value is a comma-separated list; alternative
  sets are separated by a semicolon, for example `pull_requests=read,contents=read;
  issues=read,contents=read`. [doc: TROUBLESHOOTING] On which responses (only errors, or all)
  the header appears is **[not stated]**; FG-PERMISSIONS says "you will receive the
  `X-Accepted-GitHub-Permissions` header in the REST API response".

### B.3 Personal access token (classic)

- Scopes [doc: OAUTH-SCOPES]:
  - `(no scope)`: "Grants read-only access to public information (including user profile
    info, repository info, and gists)".
  - `repo`: "Grants full access to public and private repositories including read and write
    access to code, commit statuses, repository invitations, collaborators, deployment
    statuses, and repository webhooks."
  - `public_repo`: "Limits access to public repositories." (read/write).
  - There is no read-only scope for private repositories in the table: a private repository
    needs `repo`, which includes write. [doc: OAUTH-SCOPES]
- Breadth: a classic token "will grant access to all repositories within the organizations
  that you have access to, as well as all personal repositories in your personal account."
  [doc: PAT-MANAGE]
- Classic tokens have no expiry requirement in organisations ("Personal access tokens
  (classic) do not have an expiration requirement.") [doc: PAT-ORG-POLICY] and are not subject
  to approval (B.5). Endpoint pages do not name classic scopes per endpoint **[not stated]**;
  response headers `X-OAuth-Scopes` and `X-Accepted-OAuth-Scopes` appear in the example header
  list. [example: GETTING-STARTED]
- SAML SSO: a classic token must be authorised for SSO after creation (B.6).

### B.4 GitHub App installation token

- "The installation access token will expire after 1 hour." It is obtained with `POST
  /app/installations/INSTALLATION_ID/access_tokens`, with the App's JSON web token in the
  `Authorization` header. [doc: APP-INSTALL-AUTH]
- The request body may narrow it: `repositories` or `repository_ids`, and `permissions`; "If
  `permissions` is not specified, the installation access token will have all of the
  permissions that were granted to the app." [doc: APP-INSTALL-AUTH]
- "Access tokens created by apps are automatically authorized for SAML SSO." [doc: AUTH-REST]
- The endpoint pages give the same permission names for "GitHub App installation access
  tokens" as for fine-grained tokens (table in B.1). [doc: PULLS, ISSUES, RELEASES]
- Rate limit differs (J.1). The App's private key, JWT format and lifetime were not read
  (pages not fetched): **[not stated]** here.

### B.5 Organisation controls on tokens

- PAT policy options, set by organisation owners [doc: PAT-ORG-POLICY]:
  - "Restrict access via personal access tokens: Personal access tokens (classic) or
    fine-grained personal access tokens cannot access resources owned by the organization."
    (an option, quoted from the bullet text without its bold markup)
  - "Require administrator approval: An organization owner must approve each fine-grained
    personal access token that can access the organization. Fine-grained personal access
    tokens created by organization owners will not need approval. This is the default
    value." (same)
  - "Only fine-grained personal access tokens, not personal access tokens (classic), are
    subject to approval."
- Pending tokens: "your token will be marked as `pending` until it is reviewed by an
  organization administrator. Your token will only be able to read public resources until it
  is approved." [doc: PAT-MANAGE]
- Maximum lifetime: "For fine-grained personal access tokens, the default the maximum lifetime
  policy for organizations is set to expire within 366 days." (sic) "Setting this policy does
  not revoke or disable these tokens. Users will learn that their existing token is
  non-compliant when API calls for your organization are rejected." [doc: PAT-ORG-POLICY] The
  status code and body of that rejection are **[not stated]**.
- Owners can also review and revoke approved tokens [doc: PAT-ORG-REQUESTS]; what the
  connector sees after a revoke is **[not stated]** beyond B.6's 401/403/404.
- QUESTION: how does the connector tell a token pending approval, a token revoked by the
  organisation and a token blocked by the lifetime policy apart? The pages give no distinct error for any of them.

### B.6 What failures look like

- "If you try to use a REST API endpoint without a token or with a token that has insufficient
  permissions, you will receive a `404 Not Found` or `403 Forbidden` response. Authenticating
  with invalid credentials will initially return a `401 Unauthorized` response." [doc:
  AUTH-REST]
- "After detecting several requests with invalid credentials within a short period, the API
  will temporarily reject all authentication attempts for that user (including ones with
  valid credentials) with a `403 Forbidden` response." [doc: AUTH-REST] So a scheduled job
  must stop retrying on 401.
- "GitHub uses a `404 Not Found` response instead of a `403 Forbidden` response to avoid
  confirming the existence of private repositories." [doc: TROUBLESHOOTING]
- Fine-grained or App token lacking a permission: a "Resource not accessible by personal access
  token" or "Resource not accessible by integration" error. [doc: TROUBLESHOOTING] Its status
  code is **[not stated]** on that page.
- Organisation has blocked classic tokens: "If you try to use a personal access token
  (classic) to access resources in an organization that has disabled personal access token
  (classic) access, your request will fail with a 403 response." [doc: PAT-MANAGE]
- SAML SSO, classic token not authorised: "you may receive a `404 Not Found` or a `403
  Forbidden` error. If you receive a `403 Forbidden` error, the `X-GitHub-SSO` header will
  include a URL that you can follow to authorize your token." [doc: AUTH-REST] "The URL
  expires after one hour." [doc: AUTH-REST] Fine-grained tokens "are authorized during
  token creation, before access to the organization is granted." [doc: AUTH-REST]

---

## C. Pull requests

### C.1 List pull requests: `GET /repos/{owner}/{repo}/pulls`

- operationId `pulls/list`; responses `200`, `304`, `422`. [spec]
- Query parameters [spec, doc: PULLS]:

  | Name | Type, default | Semantics as stated |
  |---|---|---|
  | `state` | `open`/`closed`/`all`, default `open` | "Either `open`, `closed`, or `all` to filter by state." |
  | `head` | string | `user:ref-name` or `organization:ref-name` |
  | `base` | string | "Filter pulls by base branch name." |
  | `sort` | `created`/`updated`/`popularity`/`long-running`, default `created` | `popularity` = number of comments; `long-running` limits to PRs open more than a month with activity in the past month |
  | `direction` | `asc`/`desc`, no schema default | "Default: `desc` when sort is `created` or sort is not specified, otherwise `asc`." |
  | `per_page` | integer, default 30 | "The number of results per page (max 100)." |
  | `page` | integer, default 1 | page number |

- **There is no `since`, no merged filter and no closed-after filter.** A merged PR is a
  `closed` PR with `merged_at` not null. [spec] With `sort=updated` and no `direction` the
  order is ascending (oldest update first). Tie-break between equal sort keys:
  **[not stated]**.
- Draft pull requests: "Draft pull requests are available in public repositories with GitHub
  Free and GitHub Free for organizations, GitHub Pro, and legacy per-repository billing plans,
  and in public and private repositories with GitHub Team and GitHub Enterprise Cloud." [doc:
  PULLS]
- Token types and public data: the page lists "GitHub App user access tokens", "GitHub App
  installation access tokens" and "Fine-grained personal access tokens", and says "This
  endpoint can be used without authentication or the aforementioned permissions if only
  public resources are requested." [doc: PULLS] (The same sentence closes the token block
  of every endpoint used here.)
- Item schema "Pull Request Simple" [spec]. Required: `_links`, `labels`, `base`, `body`,
  `closed_at`, `comments_url`, `commits_url`, `created_at`, `diff_url`, `head`, `html_url`,
  `id`, `node_id`, `issue_url`, `merged_at`, `milestone`, `number`, `patch_url`,
  `review_comment_url`, `review_comments_url`, `statuses_url`, `state`, `locked`, `title`,
  `updated_at`, `url`, `user`, `author_association`, `auto_merge`. The fields this connector
  reads:

  | Field | Type [spec] |
  |---|---|
  | `id` | integer (int64) |
  | `number` | integer |
  | `state` | string, example `open`, **no enum** in the list schema (the single GET has enum `open`, `closed`) |
  | `title` | string |
  | `body` | string, nullable |
  | `user` | nullable Simple User |
  | `labels` | array of objects: `id` int64, `node_id`, `url`, `name` string, `description` string nullable, `color`, `default` boolean |
  | `base.ref` | string (the base branch name) |
  | `created_at`, `updated_at` | date-time |
  | `closed_at`, `merged_at` | date-time, nullable |
  | `draft` | boolean (not required) |
  | `author_association` | enum `COLLABORATOR`, `CONTRIBUTOR`, `FIRST_TIMER`, `FIRST_TIME_CONTRIBUTOR`, `MANNEQUIN`, `MEMBER`, `NONE`, `OWNER` |
  | `html_url` | string, uri |

  **Not in the list item:** `merged`, `merged_by`, `comments`, `review_comments`, `commits`,
  `additions`, `deletions`, `changed_files`, `mergeable`. [spec]

### C.2 Get a pull request: `GET /repos/{owner}/{repo}/pulls/{pull_number}`

- operationId `pulls/get`; responses `200`, `304`, `404`, `406`, `422`, `500`, `503`. [spec]
- "Lists details of a pull request by providing its number." [doc: PULLS]
- Adds to C.1 [spec]: `merged` boolean; `merged_by` nullable Simple User (in `required`, so
  present and `null` when not merged); `mergeable` nullable boolean; `comments`,
  `review_comments`, `commits`, `additions`, `deletions`, `changed_files` integers;
  `maintainer_can_modify`. `user` is a non-nullable Simple User in this schema. [spec]
- In the page's Link Relations list, `comments` is "The API location of this pull request's
  issue comments" (link text). [doc: PULLS] That describes the `comments` link in `_links`,
  not the integer field. What the integer `comments` and `review_comments` count is **[not
  stated]** (by their names: conversation comments and line comments).
- `merge_commit_sha` is absent under 2026-03-10 (A.3). [doc: BREAKING]

### C.3 Check if merged: `GET /repos/{owner}/{repo}/pulls/{pull_number}/merge`

- `204` if merged; `404` "if pull request has not been merged". [doc: PULLS] Not needed when
  `merged_at` is read.

---

## D. Reviews and comments on a pull request

Three different things [doc: PULLS, REVIEWS, REVIEW-COMMENTS, ISSUE-COMMENTS]:

- **Conversation comments** (the PR's main thread) are issue comments: "Every pull request is
  an issue, but not every issue is a pull request." Use the issue comment endpoints with the
  PR number (E.4). [doc: ISSUE-COMMENTS]
- **Reviews** carry the review summary: "Pull Request Reviews are groups of pull request review
  comments on a pull request, grouped together with a state and optional body comment." [doc:
  REVIEWS]
- **Review comments** are line comments: "Pull request review comments are comments made on a
  portion of the unified diff during a pull request review. These are different from commit
  comments and issue comments in a pull request." [doc: REVIEW-COMMENTS]

### D.1 List reviews: `GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews`

- "Lists all reviews for a specified pull request. The list of reviews returns in
  chronological order." [doc: REVIEWS] Responses: `200` only. [spec] Parameters: `per_page`
  (default 30, max 100), `page`. [spec]
- Media types: `application/vnd.github-commitcomment.raw+json` (default, `body`),
  `.text+json` (`body_text`), `.html+json` (`body_html`), `.full+json` (all three) (names as
  listed on the page). [doc: REVIEWS]
- Item schema "Pull Request Review" [spec]. Required: `id`, `node_id`, `user`, `body`,
  `state`, `commit_id`, `html_url`, `pull_request_url`, `_links`, `author_association`.

  | Field | Type [spec] |
  |---|---|
  | `id` | integer (int64) |
  | `user` | nullable Simple User |
  | `body` | string, required, "The text of the review." |
  | `state` | string, example `CHANGES_REQUESTED`, **no enum** |
  | `submitted_at` | date-time, **not required** |
  | `commit_id` | string, nullable |
  | `html_url` | uri |
  | `author_association` | enum as in C.1 |

- `commit_id`: "If the commit object was garbage collected or forcibly deleted, then it no
  longer exists in Git and this value will be `null`." [spec]
- State values: example responses show `APPROVED`, `CHANGES_REQUESTED`, `DISMISSED` (upper
  case). [example: REVIEWS] Parameter text elsewhere on the page names `PENDING`: "Pull request
  reviews created in the PENDING state are not submitted and therefore do not include the
  submitted_at property in the response." [doc: REVIEWS] **CONFLICT** (spelling): the timeline
  `reviewed` event says "Can be one of: `commented`, `changes_requested`, `approved` or
  `dismissed`." [doc: EVENT-TYPES] An upper-case `COMMENTED` value: zero matches in
  REVIEWS, TIMELINE and SPEC (grep), so **[not stated]**.
- QUESTION: does the list show other users' `PENDING` reviews, and is a review with no text
  (a bare approval) returned with `body` as an empty string? Not stated.

### D.2 List review comments on a PR: `GET /repos/{owner}/{repo}/pulls/{pull_number}/comments`

- "By default, review comments are in ascending order by ID." [doc: REVIEW-COMMENTS]
- Parameters: `sort` (`created`, `updated`; default `created`), `direction` ("Ignored
  without `sort` parameter."), `since` ("Only show results that were last updated after the
  given time."), `per_page`, `page`. [spec] Responses: `200`. [spec]
- Fields: `id`, `pull_request_review_id` (nullable integer), `in_reply_to_id`, `body`,
  `user`, `path`, `diff_hunk`, `created_at`, `updated_at`, `html_url`,
  `author_association`. [spec] Repository-wide variant: `GET
  /repos/{owner}/{repo}/pulls/comments`, same order statement. [doc: REVIEW-COMMENTS]
- The orchestrator asked for review **summary** comments; line comments are listed here so the
  design can include or exclude them knowingly.

---

## E. Issues

### E.1 List repository issues: `GET /repos/{owner}/{repo}/issues`

- operationId `issues/list-for-repo`; responses `200`, `301`, `404`, `422` (no `304` listed).
  [spec]
- First sentence: "List issues in a repository. Only open issues will be listed." [doc:
  ISSUES] **CONFLICT** with the `state` parameter, "Indicates the state of the issues to
  return.", enum `open`, `closed`, `all`, default `open`. [spec] The likely reading is that the
  sentence describes the default; the page does not say so.
- Pull requests are included: "GitHub's REST API considers every pull request an issue, but not
  every issue is a pull request. For this reason, "Issues" endpoints may return both issues and
  pull requests in the response. You can identify pull requests by the pull_request key." (the
  inner quotes are the page's own) [doc: ISSUES] A filter that excludes pull requests is
  **[not stated]** (none exists among the parameters). [spec]
- Query parameters [spec]:

  | Name | Type, default | Semantics as stated |
  |---|---|---|
  | `state` | `open`/`closed`/`all`, default `open` | as above |
  | `labels` | string | "A list of comma separated label names." |
  | `creator`, `mentioned`, `assignee`, `milestone`, `type`, `issue_field_values` | string | filters, not needed here |
  | `sort` | `created`/`updated`/`comments`, default `created` | "What to sort results by." |
  | `direction` | `asc`/`desc`, default `desc` | "The direction to sort the results by." |
  | `since` | string, `format: date-time` | "Only show results that were last updated after the given time. This is a timestamp in ISO 8601 format: `YYYY-MM-DDTHH:MM:SSZ`." |
  | `per_page` | integer, default 30 | max 100 |
  | `page` | integer, default 1 | |

- `since` filters on `updated_at`; whether the bound is inclusive is **[not stated]**; it does
  not change the sort. [spec]
- Item schema "Issue" (the same schema as the single GET) [spec]. Required: `closed_at`,
  `comments`, `comments_url`, `events_url`, `html_url`, `id`, `node_id`, `labels`,
  `labels_url`, `milestone`, `number`, `repository_url`, `state`, `locked`, `title`, `url`,
  `user`, `created_at`, `updated_at` (SPEC-2022 also requires `assignee`). Fields this connector
  reads:

  | Field | Type [spec] |
  |---|---|
  | `id` | integer (int64); for a PR this is the issue id, not the PR id [doc: ISSUES] |
  | `number` | integer |
  | `state` | string, "State of the issue; either 'open' or 'closed'", **no enum** |
  | `state_reason` | string, nullable, enum `completed`, `reopened`, `not_planned`, `duplicate`; "The reason for the current state" |
  | `title` | string |
  | `body` | string, nullable, **not required** |
  | `user` | nullable Simple User |
  | `labels` | array; each item is **either a string or an object** (`id`, `node_id`, `url`, `name`, `description`, `color`, `default`) |
  | `closed_at` | date-time, nullable |
  | `closed_by` | nullable Simple User, not required |
  | `created_at`, `updated_at` | date-time |
  | `locked` | boolean; `active_lock_reason` string, nullable |
  | `author_association` | enum as in C.1 |
  | `pull_request` | object, present only on pull requests (A.3 for its fields) |
  | `html_url` | uri |

- The close reason the orchestrator wants maps to `state_reason`: `completed` and
  `not_planned`; the enum also has `duplicate` and `reopened`. [spec] CLOSE-ISSUE says only
  "You can close an issue when bugs are fixed, feedback is acted on, or to show that work is
  not planned." (the reason menu is shown in a screenshot). Whether old issues closed before close reasons existed have `state_reason:
  null` is **[not stated]**.
- Whether the list fills `closed_by` (a "summary representation" may omit costly fields,
  A.4) is **[not stated]**. QUESTION: is `closed_by` populated in the list, or only on `GET
  /issues/{n}`?

### E.2 Get an issue: `GET /repos/{owner}/{repo}/issues/{issue_number}`

- Responses `200`, `301`, `304`, `404`, `410`. [spec]
- "The API returns a `301 Moved Permanently` status if the issue was transferred to another
  repository. If the issue was transferred to or deleted from a repository where the
  authenticated user lacks read access, the API returns a `404 Not Found` status. If the issue
  was deleted from a repository where the authenticated user has read access, the API returns
  a `410 Gone` status." [spec; the same text on ISSUES]
- Note the wording "transferred to or deleted from a repository where the authenticated user
  lacks read access": a transfer to a repository the token cannot read gives 404, not 301.

### E.3 What happens to an issue (lifecycle facts used by E.1 and E.2)

- Transfer: "You can only transfer issues between repositories owned by the same user or
  organization account." "When you transfer an issue, comments and assignees are retained."
  "The original URL redirects to the new issue's URL." People without read access to the new
  repository "will see a banner letting them know that the issue has been transferred to a
  new repository that they can't access." [doc: TRANSFER-ISSUE] Whether a transferred issue
  keeps appearing in the old repository's issue list is **[not stated]**; GET gives 301 or 404
  (E.2).
- Deletion: "People with admin permissions in a repository can permanently delete an issue
  from a repository." Collaborators visiting its URL see a not-found page "(but they can use
  the API to determine that it was deleted)". [doc: DELETE-ISSUE] That a deleted issue
  disappears from the list is **[not stated]** (implied by the 410 on GET).
- Converted to a discussion: the `converted_to_discussion` event, "The issue was closed and
  converted to a discussion." [doc: EVENT-TYPES] What `GET /issues/{n}` returns afterwards,
  and which `state_reason` it gets, are **[not stated]**.

### E.4 Comments on an issue or a pull request

- Per item: `GET /repos/{owner}/{repo}/issues/{issue_number}/comments` (works with a PR number).
  "Issue comments are ordered by ascending ID." [doc: ISSUE-COMMENTS] Parameters: `since`
  (last updated after), `per_page`, `page`. Responses `200`, `404`, `410`. [spec]
- Whole repository: `GET /repos/{owner}/{repo}/issues/comments`. "By default, issue comments
  are ordered by ascending ID." [doc: ISSUE-COMMENTS] Parameters: `sort` (`created`,
  `updated`; default `created`), `direction` ("Either `asc` or `desc`. Ignored without the
  `sort` parameter."), `since`, `per_page`, `page`. Responses `200`, `404`, `422`. [spec]
- Permission: Issues (read) or Pull requests (read). [doc: ISSUE-COMMENTS]
- Item schema "Issue Comment" [spec]. Required: `id`, `node_id`, `html_url`, `issue_url`,
  `user`, `url`, `created_at`, `updated_at`. Fields: `id` int64, `body` string (**not
  required**), `user` nullable Simple User, `created_at`, `updated_at` date-time,
  `author_association`, `html_url`, `issue_url` (links back to the issue or PR; the
  repository-wide list has no issue number field of its own), `minimized` (nullable object
  with a `reason`, for hidden comments), `pin`, `reactions`, `performed_via_github_app`. [spec]
- Edited comments keep their `id`; `updated_at` moves (implied by the `since` semantics;
  **[not stated]** as a rule). Edit history is UI-only (L.3).

### E.5 Issue events and the timeline

- `GET /repos/{owner}/{repo}/issues/events` ("Lists events for a repository."), parameters
  `per_page`, `page`; responses `200`, `422`; permission Issues (read). Order **[not
  stated]**. [doc: ISSUE-EVENTS; spec]
- `GET /repos/{owner}/{repo}/issues/{issue_number}/events`; responses `200`, `410`. [spec]
- `GET /repos/{owner}/{repo}/issues/{issue_number}/timeline`, parameter `exclude` ("A
  comma-separated list of timeline event names to exclude from the response."), `per_page`,
  `page`; responses `200`, `400`, `404`, `410`; permission Issues or Pull requests (read).
  [spec] Order **[not stated]**.
- Event object (repository and per-issue lists, schema "Issue Event") [spec]: `id`, `node_id`,
  `url`, `actor` (nullable Simple User), `event` (string, example `closed`, **no enum**),
  `commit_id`, `commit_url` (nullable), `created_at` (date-time), `issue` (the issue object, in
  the repository-wide list), `lock_reason`, `performed_via_github_app` and per-type extras.
  It has **no `state_reason`**; the timeline's "State Change Issue Event" does. [spec]
- Event types that matter here [doc: EVENT-TYPES]:
  - `closed`: "The issue or pull request was closed. When the `commit_id` is present, it
    identifies the commit that closed the issue using" closes/fixes syntax (in both lists).
  - `merged`: "The pull request was merged. The `commit_id` attribute is the SHA1 of the `HEAD`
    commit that was merged." Its `actor` is the person who generated the event (common
    property: "The person who generated the event."). That this actor equals `merged_by` is
    **[not stated]**.
  - `reopened`, `transferred` ("The issue was transferred to another repository."),
    `converted_to_discussion`, `locked` (with `lock_reason`), `marked_as_duplicate`.
  - `commented` and `reviewed` appear only in the timeline. The `reviewed` event carries
    `body` ("The review summary text."), `state`, `submitted_at`, `user`. [doc: EVENT-TYPES]
- Common properties: `commit_id` is "The SHA of the commit that referenced this issue." and
  `created_at` is "The timestamp indicating when the event occurred." [doc: EVENT-TYPES]
- How long repository-wide events are kept is **[not stated]** in the pages read.
  QUESTION: is `GET /issues/events` complete over the life of the repository, or truncated?

---

## F. Releases

### F.1 List releases: `GET /repos/{owner}/{repo}/releases`

- "This returns a list of releases, which does not include regular Git tags that have not been
  associated with a release." [doc: RELEASES]
- "Information about published releases are available to everyone. Only users with push access
  will receive listings for draft releases." [doc: RELEASES] The page speaks of users with push
  access only; whether a token with Contents (read) whose owner has push access sees drafts,
  and whether a read-only token does not, are **[not stated]**.
- Parameters: `per_page` (default 30, max 100), `page`. Responses `200`, `404`. Permission:
  Contents (read). [spec; doc: RELEASES]
- **Order: [not stated].** No `sort`, no `since`. QUESTION: is the list newest first, and by
  which timestamp?
- Item schema "Release" [spec]. Required: `assets_url`, `upload_url`, `tarball_url`,
  `zipball_url`, `created_at`, `published_at`, `draft`, `id`, `node_id`, `author`, `html_url`,
  `name`, `prerelease`, `tag_name`, `target_commitish`, `assets`, `url`.

  | Field | Type [spec] |
  |---|---|
  | `id` | integer |
  | `tag_name` | string, "The name of the tag." |
  | `name` | string, nullable |
  | `body` | string, nullable, **not required** |
  | `body_html`, `body_text` | string, not required (no media type for them is listed on RELEASES) |
  | `draft` | boolean |
  | `prerelease` | boolean |
  | `immutable` | boolean, "Whether or not the release is immutable." |
  | `created_at` | date-time |
  | `published_at` | date-time, nullable (required: present, `null` for drafts by implication) |
  | `updated_at` | date-time, nullable, not required |
  | `author` | Simple User (**not** nullable) |
  | `html_url` | uri |
  | `target_commitish` | string |

- What `created_at` means: "The `created_at` attribute is the date of the commit used for the
  release, and not the date when the release was drafted or published." (said in the
  description of Get the latest release) [doc: RELEASES; spec] So published-after-T must use
  `published_at`, not `created_at`.
- Get latest: `GET /repos/{owner}/{repo}/releases/latest`, "The latest release is the most
  recent non-prerelease, non-draft release, sorted by the `created_at` attribute." Responses
  `200`, `404`. [spec]
- Get one: `GET /repos/{owner}/{repo}/releases/{release_id}`, "Gets a public release with the
  specified release ID." Responses listed: `200`, `401` only. [spec] A `404` for a deleted
  release is **[not stated]**.
- Deleting a release is possible from the UI (MANAGE-RELEASES); what the list or GET shows
  afterwards is **[not stated]** (it presumably disappears). Whether `published_at` changes
  when a release is unpublished to a draft and published again is **[not stated]**.

---

## G. Finding items finished after T (routes, none chosen)

The documentation offers no single listing of items merged, closed or published after a
time. These are the documented routes; the choice is the design's.

1. **Pull requests by update time.** `GET /pulls?state=closed&sort=updated&direction=desc`,
   page until `updated_at` is older than T, keep items with `merged_at >= T`. Depends on a
   merge moving `updated_at` **[not stated]**. BEST-PRACTICES warns that `sort=updated`
   reorders the list "whenever an item changes" so paging can skip or repeat items (I.2).
   Then one `GET /pulls/{n}` per merged PR for `merged_by` (C.2).
2. **Issues by update time.** `GET /issues?state=closed&since=T&sort=updated` (returns PRs
   too; drop items with `pull_request` or use them as a second PR route). `since` is "last
   updated after", inclusive or not **[not stated]**; keep items with `closed_at >= T` and
   `pull_request` absent. Same `updated_at` dependency. Closing an issue presumably moves
   `updated_at`; **[not stated]**.
3. **Repository issue events.** `GET /issues/events` lists `closed` and `merged` events with
   `created_at` and `actor` [doc: EVENT-TYPES], but has no `since` and its order is **[not
   stated]**; it also carries no `state_reason` (E.5).
4. **Search.** `GET /search/issues?q=repo:OWNER/REPO is:pr is:merged merged:>=YYYY-MM-DDTHH:MM:SS+00:00`
   and `q=repo:OWNER/REPO is:issue is:closed closed:>=...`, `sort=updated` or `created`
   (there is no sort by merged or closed time). Limits in H.

Facts the routes share:

- Reading each item only once needs a stable key: `id` (int64) for releases, reviews and comments;
  for issues and PRs the `number` is unique in a repository: "Because pull requests are issues,
  issue and pull request numbers do not overlap in a repository." [doc: EVENT-TYPES] A
  transferred issue gets a number in the new repository (implied by TRANSFER-ISSUE; not
  stated).
- A PR can be closed, reopened and merged later; an issue can be closed, reopened and closed
  again with a different `state_reason`. The pages do not say whether `closed_at` is
  overwritten on the second close (**[not stated]**).
- Releases: paging `GET /releases` until `published_at` is older than T works only if the
  list is ordered by publish time, which is **[not stated]** (F.1).

---

## H. Search: `GET /search/issues`

- operationId `search/issues-and-pull-requests`; responses `200`, `304`, `401`, `403`, `422`,
  `503`. [spec]
- Result cap: the API "provides up to 1,000 results for each search". [doc: SEARCH] What
  happens when paging past 1,000 is **[not stated]**.
- Rate: "30 requests per minute for all search endpoints except for the Search code endpoint"
  (the page's link text), and unauthenticated "up to 10 requests per minute". [doc: SEARCH]
  Its own bucket in `/rate_limit` is `search`. [doc: RATE-LIMIT-EP]
- Query limits: queries may not be "longer than 256 characters (not including operators or
  qualifiers)" or have "more than five `AND`, `OR`, or `NOT` operators". [doc: SEARCH] "The
  REST API will find up to 4,000 repositories that match your filters and return results from
  those repositories." [doc: SEARCH]
- Timeouts: on a timeout "the response has the `incomplete_results` property set to `true`".
  [doc: SEARCH]
- Access: "You need to successfully authenticate and have access to the repositories in your
  search queries, otherwise, you'll see a `422 Unprocessable Entry` error with a "Validation
  Failed" message." (sic: "Entry") [doc: SEARCH] Permission: "The fine-grained token does not
  require any permissions." [doc: SEARCH] QUESTION (headline 8): can a fine-grained token
  limited to one private repository find its items through search?
- GitHub App user tokens: "Requests that don't include the is:issue or is:pull-request
  qualifier will receive an HTTP 422 Unprocessable Entity response." [doc: SEARCH]
- Parameters [spec]: `q` (required); `sort` (`comments`, `reactions`, ..., `interactions`,
  `created`, `updated`; default best match); `order` (`desc`/`asc`, default `desc`, "This
  parameter is ignored unless you provide `sort`."); `per_page` (max 100); `page`;
  `advanced_search`; `search_type` (`semantic`, `hybrid`; "Semantic and hybrid search require
  authentication and are rate limited to 10 requests per minute.").
- Response [spec]: `total_count` integer, `incomplete_results` boolean, `items` (issue search
  result items: issue fields plus `score`), `search_type` (enum `lexical`, `semantic`,
  `hybrid`), `lexical_fallback_reason`.
- Qualifiers [doc: SEARCH-ISSUES]:
  - `repo:OWNER/REPO`, `is:pr`/`type:pr`, `is:issue`, `is:closed`/`state:closed`,
    `is:merged` ("matches merged pull requests"), `is:unmerged` ("either open or were closed
    without being merged").
  - `reason:completed`, `reason:"not planned"` (closed issues by reason).
  - `closed:`, `merged:`, `created:`, `updated:`: "Date formatting must follow the ISO8601
    standard, which is `YYYY-MM-DD` (year-month-day). You can also add optional time
    information `THH:MM:SS+00:00` after the date, to search by the hour, minute, and
    second." (link stripped)
  - `is:archived` (archived pull requests, triage access needed), `archived:true|false`
    (archived repositories).
- Operators [doc: SEARCH-SYNTAX]: "You can use `>`, `>=`, `<`, and `<=` to search for values
  that are greater than, greater than or equal to, less than, and less than or equal to
  another value." Example: `created:>=2017-04-01` matches items "that were created on or
  after April 1, 2017." Ranges `a..b`, `a..*`, `*..b`.
- **CONFLICT**: SEARCH-ISSUES gives `merged:>=2014-05-01` as matching pull requests "that were
  merged after May 2014", while SEARCH-SYNTAX defines `>=` as "greater than or equal to".
- The time zone of a date-only value is **[not stated]**. Index freshness (how soon a merged
  PR becomes searchable) is **[not stated]**.
- "If you have access to pull requests in more than 10,000 repositories, you will need to
  limit your search to a specific organization, personal account, or repository to see
  results." [doc: SEARCH-ISSUES]

---

## I. Paging and conditional requests

### I.1 Paging

- "You can use the `link` header from the response to request additional pages of data."
  [doc: PAGINATION] Relations: `prev`, `next`, `last`, `first`; "the link to the previous page
  won't be included if you are on the first page of results, and the link to the last page
  won't be included if it can't be calculated." [doc: PAGINATION]
- "each paginated endpoint will use the `page`, `before`/`after`, or `since` query parameters.
  (Some endpoints use the `since` parameter for something other than pagination.)" [doc:
  PAGINATION] Every endpoint in this note uses `page`. [spec]
- Example: `link: <https://api.github.com/repositories/1300192/issues?per_page=2&page=2>;
  rel="next", <...&page=7715>; rel="last"` (the next URL uses the repository's numeric id, not
  owner/name). [example: PAGINATION]
- "For most endpoints, the maximum value of `per_page` is `100`. If you specify a value greater
  than the maximum, GitHub does not return an error." "Because the request still succeeds, you
  may receive fewer results than you expect without any indication that the `per_page` value
  was reduced." [doc: PAGINATION]
- Follow the `link` URLs rather than building them: "you should not try to manually
  construct pagination queries." [doc: BEST-PRACTICES]

### I.2 Paging a list that changes

- "If you page through a list, use a stable sort order. Some parameters, such as
  `sort=updated`, reorder the list whenever an item changes." "A stable order, such as the
  default, stops updates to existing items from reordering the list, although adding or
  removing items can still shift entries onto other pages." [doc: BEST-PRACTICES]
- "Use the same parameters every time you poll the same data. A different page size, page
  number, or filter produces a different response with a different `etag`." [doc:
  BEST-PRACTICES]

### I.3 Conditional requests

- "Most endpoints return an `etag` header, and many endpoints return a `last-modified` header."
  Send `if-none-match` or `if-modified-since`; an unchanged answer is `304 Not Modified`.
  [doc: BEST-PRACTICES]
- "Making a conditional request does not count against your primary rate limit if a `304`
  response is returned and the request was made while correctly authorized with an
  `Authorization` header." [doc: BEST-PRACTICES]
- "The endpoint returns `304` when the representation that you requested has not changed since
  you saved its `etag` or `last-modified` value; unrelated response headers, such as the date,
  can still differ." [doc: BEST-PRACTICES]
- SPEC lists `304` for the pull request list and GET, the issue GET, search and `/rate_limit`,
  but **not** for the issue list, comments, reviews, events or releases. [spec] QUESTION:
  do those endpoints honour `if-none-match`? (SPEC-README: not all headers are described.)
- Polling: "If a response includes an `x-poll-interval` header, wait at least that many seconds
  before you poll the same endpoint again." [doc: BEST-PRACTICES] Which endpoints send it is
  **[not stated]**.
- "You should subscribe to webhook events instead of polling the API for data." [doc:
  BEST-PRACTICES] (Webhooks need an inbound endpoint; out of scope for a server that reads on a
  schedule.)

---

## J. Rate limits

### J.1 Primary limits [doc: RATE-LIMITS]

- "The primary rate limit for unauthenticated requests is 60 requests per hour."
- Personal access tokens (classic or fine-grained) count against the user: "your personal rate
  limit of 5,000 requests per hour". "Requests authenticated as a user share the user's rate
  limit across credentials." [doc: RATE-LIMIT-EP] So the connector shares its budget with
  everything else the token's owner runs.
- Installation tokens: "the installation's minimum rate limit of 5,000 requests per hour"; on
  a GitHub Enterprise Cloud organisation 15,000. Otherwise it grows: "Installations that have
  more than 20 repositories receive another 50 requests per hour for each repository."
  "Installations that are on an organization that have more than 20 users receive another 50
  requests per hour for each user. The rate limit cannot increase beyond 12,500 requests per
  hour."
- "The rate limit for `GITHUB_TOKEN` is 1,000 requests per hour per repository." (not this
  connector's token)
- Search has its own bucket: 30 a minute (H).

### J.2 Secondary limits [doc: RATE-LIMITS]

- "No more than 100 concurrent requests are allowed."
- "No more than 900 points per minute are allowed for REST API endpoints". "Most REST API `GET`, `HEAD`, and `OPTIONS` requests" cost 1 point (points table).
- "No more than 90 seconds of CPU time per 60 seconds of real time is allowed."
- 80 content-generating requests a minute and 500 an hour (writes; not this connector).
- "These secondary rate limits are subject to change without notice. You may also encounter a
  secondary rate limit for undisclosed reasons." "There is not a way to check the status of
  your secondary rate limit."
- BEST-PRACTICES: "you should make requests serially instead of concurrently."

### J.3 Headers [doc: RATE-LIMITS]

| Header | Meaning (quoted) |
|---|---|
| `x-ratelimit-limit` | "The maximum number of requests that you can make per hour" |
| `x-ratelimit-remaining` | "The number of requests remaining in the current rate limit window" |
| `x-ratelimit-used` | "The number of requests you have made in the current rate limit window" |
| `x-ratelimit-reset` | "The time at which the current rate limit window resets, in UTC epoch seconds" |
| `x-ratelimit-resource` | "The rate limit resource that the request counted against." |
| `retry-after` | seconds to wait, on secondary limits (J.4) |

- "rate limit values can vary from one response to the next based on the location handling the
  request." "The `x-ratelimit-*` headers are the authoritative source for your current rate
  limit status and may differ from values reported by the `GET /rate_limit` endpoint."
- Example value `X-Ratelimit-Limit: 15000` appears on the getting-started page. [example:
  GETTING-STARTED]

### J.4 Exceeding a limit [doc: RATE-LIMITS]

- Primary: "If you exceed your primary rate limit, you will receive a `403` or `429` response,
  and the `x-ratelimit-remaining` header will be `0`. You should not retry your request until
  after the time specified by the `x-ratelimit-reset` header."
- Secondary: "you will receive a `403` or `429` response and an error message that indicates
  that you exceeded a secondary rate limit. If the `retry-after` response header is present,
  you should not retry your request until after that many seconds has elapsed. If the
  `x-ratelimit-remaining` header is `0`, you should not retry your request until after the
  time, in UTC epoch seconds, specified by the `x-ratelimit-reset` header. Otherwise, wait for
  at least one minute before retrying." Then "wait for an exponentially increasing amount of
  time between retries, and throw an error after a specific number of retries." [doc:
  BEST-PRACTICES]
- "Continuing to make requests while you are rate limited may result in the banning of your
  integration."
- The JSON body of a rate-limit refusal and its exact message are **[not stated]**; SPEC
  declares no `429` response on any endpoint used here. [spec] A 403 for a rate limit and a
  403 for permissions must be told apart by the headers (`x-ratelimit-remaining: 0`,
  `retry-after`) or the message text.

### J.5 `GET /rate_limit` [doc: RATE-LIMIT-EP; spec]

- "Accessing this endpoint does not count against your REST API rate limit." (it "can count
  against your secondary rate limit" [doc: RATE-LIMITS]). Responses `200`, `304`, `404`.
- Response: `resources` with `core`, `search`, `code_search`, `graphql` and others, each
  `{limit, remaining, reset, used, resource}` (paraphrase of the schema); the top-level `rate`
  is gone in 2026-03-10 (A.3).
- GHES: "Rate limits are disabled by default for GitHub Enterprise Server." [doc:
  GHES-RATE-LIMITS]

---

## K. Errors

- Body shapes [spec]:
  - **Basic Error** (used for `301`, `401`, `403`, `404`, `410`, `500`): `message`,
    `documentation_url`, `url`, `status`, all strings, none required.
  - **Validation Error** (`422`, "Validation failed, or the endpoint has been spammed."):
    `message`, `documentation_url` (both required), `errors`: array of objects with `code`
    (required), `resource`, `field`, `message`, `index`, `value`.
  - **503** "Service unavailable": `code`, `message`, `documentation_url`.
  - `304` has no body.
- Validation error codes [doc: TROUBLESHOOTING]: `missing` ("A resource does not exist."),
  `missing_field`, `invalid`, `already_exists`, `unprocessable`, `custom` ("Refer to the
  `message` property to diagnose the error.").
- Other documented messages [doc: TROUBLESHOOTING]: "If you send invalid JSON in the request
  body, you may receive a `400 Bad Request` response and a "Problems parsing JSON" error
  message."; "If you omit required parameters or you use the wrong type for a parameter, you
  may receive a `422 Unprocessable Entity` response and an "Invalid request" error message.";
  "If you specify a version that does not exist, you will receive a `400 Bad Request` error and
  a message about the version not being supported."
- Status codes the connector must tell apart, from the sections above:

  | Status | When (section) |
  |---|---|
  | 301 | issue transferred (E.2); any resource moved (A.1) |
  | 304 | conditional request, unchanged (I.3) |
  | 400 | unknown API version (A.3) |
  | 401 | invalid credentials, initially (B.6) |
  | 403 | invalid User-Agent (A.2); too many bad-credential attempts (B.6); missing permission or no token, sometimes (B.6); SSO not authorised (B.6); classic tokens blocked (B.6); rate limits (J.4) |
  | 404 | private resource without access; missing permission; trailing slash; unsupported method; issue moved to or deleted from a repo the token cannot read; archived PR for non-triage users (A, B, E, L) |
  | 410 | retired API version (A.3); issue deleted, token can read the repo (E.2); issues disabled |
  | 422 | validation; search without access to a `repo:` (H) |
  | 429 | rate limits (J.4) |
  | 500, 503 | listed on the PR GET and search (C.2, H) |

- Exact `message` strings such as "Bad credentials" or "Not Found" are **[not stated]** in the
  pages or SPEC (zero matches in SPEC for "Bad credentials"); the fake should not rely on them.
- Issues disabled: "If issues are disabled in the repository, the API returns a 410 Gone
  status." (said on the create endpoint) [doc: ISSUES] Whether the issue list also answers 410
  then is **[not stated]**.
- Abuse: "Intentionally ignoring repeated validation errors may result in the suspension of
  your app for abuse." [doc: BEST-PRACTICES]
- Repeated 404s: "If you are polling and a resource repeatedly returns a `404 Not Found`
  response, do not keep requesting it on every poll." A 404 "does not always mean that the
  resource is absent." [doc: BEST-PRACTICES]

---

## L. How deleted, transferred, locked, hidden and archived items look

### L.1 Issues and pull requests

- Transferred issue: 301 on GET (or 404 if the target repository is unreadable) (E.2,
  E.3); a `transferred` event exists ("The issue was transferred to another repository.").
  [doc: EVENT-TYPES] Pull requests cannot be transferred (TRANSFER-ISSUE is about issues only;
  not stated as a rule).
- Deleted issue: 410 on GET when the token can read the repository; 404 otherwise (E.2).
  Pull requests cannot be deleted through the pages read (**[not stated]**).
- Converted to a discussion: closed, with a `converted_to_discussion` event (E.3).
- Locked: "Locking a conversation creates a timeline event that is visible to anyone with read
  access to the repository." For people outside the listed groups "the locking actor will be
  anonymized". [doc: LOCK] The issue object has `locked` and `active_lock_reason`; the event
  has `lock_reason`. [spec; doc: EVENT-TYPES] "Conversations are automatically locked in
  archived repositories." [doc: LOCK] Whether that sets `locked: true` on every issue of an
  archived repository is **[not stated]**.
- Archived pull request (moderation feature, not repository archiving): "The pull request is
  only visible to people with triage access or higher to the repository. Visitors without this
  access receive a 404 error." "The pull request is automatically closed and its conversation
  becomes read-only." [doc: ARCHIVE-PRS] It is closed **without** merge, so it is not a merged
  PR. How it appears in the pull request list to a token without triage access, and whether
  the API reports an archived flag, are **[not stated]**. Search can find them with
  `is:archived` (triage access). [doc: SEARCH-ISSUES]

### L.2 Comments

- Hidden comments: "Hidden comments are minimized but people with read access to the repository
  can expand them." [doc: MANAGE-COMMENTS] The comment object has `minimized` (nullable, with
  a reason). [spec] Whether the API returns the hidden comment's `body` is **[not stated]**.
- Deleted comments: "Deleting a comment creates a timeline event that is visible to anyone with
  read access to the repository. However, the username of the person who deleted the comment is
  only visible to people with write access to the repository." [doc: MANAGE-COMMENTS] The
  comment disappears from the list (implied; **[not stated]**). The deletion event's name in
  the API is **[not stated]**.
- "The initial comment (or body) of an issue or pull request can't be deleted. Instead, you can
  edit issue and pull request bodies to remove unwanted content." [doc: MANAGE-COMMENTS]
- Edits: "anyone with read access to a repository can view a comment's edit history" (in the
  UI) [doc: MANAGE-COMMENTS]; no edit-history endpoint was in the pages read **[not stated]**.
  An edit of a body already ingested shows only as a new `updated_at`.

### L.3 People

- `user`, `actor`, `closed_by`, `merged_by` are nullable Simple Users; `author` on a release
  is not nullable. [spec] When they are `null` is **[not stated]**.
- Simple User fields: `login` (string), `id` (int64), `name` (nullable), **`email`
  (nullable)**, `type` (string, example `User`, **no enum**), `site_admin`, `html_url`,
  `avatar_url`, `user_view_type`. [spec] The connector must drop `email` even if filled.
- Bots and other kinds: other SPEC schemas enumerate `type` as `Bot`, `User`, `Organization`
  (and `Mannequin` in webhook schemas). [spec] One SPEC description shows a bot login of the form
  `copilot-swe-agent[bot]` ("include the bot login (for example, `copilot-swe-agent[bot]`)");
  that every bot login ends in `[bot]` is **[not stated]**. `author_association` has `MANNEQUIN`.
- Deleted accounts: on account conversion to an organisation, existing comments "will be
  associated with the `ghost` user". [doc: ACCOUNT-REF] For account **deletion** the pages read
  say what is deleted ("All repositories, forks of private repositories, wikis, issues, pull
  requests and GitHub Pages sites owned by your account will be deleted.") and "Your username
  will be available for anyone to use after 90 days." [doc: ACCOUNT-REF] What a deleted
  author's comments on another repository show in the API (`null`, or a `ghost` user) is
  **[not stated]**.
- Display names: `name` is in the Simple User schema but is not required and is nullable;
  whether list endpoints fill it is **[not stated]**. In practice the dependable name is
  `login`.

### L.4 Repositories

- Archived: "You can archive a repository to make it read-only for all users". "When a
  repository is archived, its issues, pull requests, code, labels, milestones, projects, wiki,
  releases, commits, tags, branches, reactions, code scanning alerts, comments and permissions
  become read-only." [doc: ARCHIVING] Reading still works (implied; **[not stated]** for the
  API). Search qualifier `archived:true|false`. [doc: SEARCH-ISSUES]
- Visibility: public or private; "Repositories in organizations that use GitHub Enterprise Cloud
  and are owned by an enterprise account can also be created with internal visibility." [doc:
  REPO-VISIBILITY] Internal resources need SSO authorisation for a classic token (B.6). [doc:
  SAML-PAT]
- Repository renamed or transferred: requests to the old path are redirected (A.1, 301);
  the pages read do not say this for repositories specifically **[not stated]**. The `link`
  header uses `/repositories/{id}/...` URLs (I.1), which survive a rename.
- Issues disabled: 410 (K).

---

## M. Bodies, media types and attachments

### M.1 Body formats

- PR and issue endpoints: "application/vnd.github.raw+json: Returns the raw Markdown body.
  Response will include body. This is the default if you do not pass any specific media
  type." Also `.text+json` (`body_text`), `.html+json` ("Returns HTML rendered from the body's
  markdown. Response will include body_html."), `.full+json` ("Returns raw, text, and HTML
  representations. Response will include body, body_text, and body_html."). [doc: PULLS,
  ISSUES, ISSUE-COMMENTS]
- Reviews use `application/vnd.github-commitcomment.*` names (D.1). Releases list no media
  types, yet the schema has `body_html` and `body_text`. [doc: RELEASES; spec]
- The `X-GitHub-Media-Type` response header names the media type served. [example:
  GETTING-STARTED]
- Maximum body length: **[not stated]**. SPEC sets no `maxLength` on issue, PR, comment, review
  or release bodies (the only body `maxLength` found, 65535, is on gist comments). [spec]
- Bodies are Markdown and may contain `@mentions`, which are logins of other people; the docs
  say nothing about stripping them **[not stated]**.

### M.2 Attachments and images in bodies

- "For public repositories, uploaded files can be accessed without authentication. In the case
  of private and internal repositories, only people with access to the repository can view the
  uploaded files." [doc: ATTACHING-FILES] Upload limits: 10MB images, 10MB or 100MB videos
  (free or paid plan), 25MB other files. [doc: ATTACHING-FILES]
- "Files and images hosted under `github.com/user-attachments/` are separate from the comments
  and pull request descriptions that link to them. Removing a link from an issue comment or
  pull request description does not necessarily delete the uploaded file." [doc: ANON-URLS]
- "Anyone who receives your anonymized URL, directly or indirectly, may view your image or
  video." [doc: ANON-URLS] Private-repository images use URLs like
  `https://private-user-images.githubusercontent.com/10001/20002.png?jwt=ABC10001` (an
  example on IP-ALLOW, which says the allow list does not restrict them). [example: IP-ALLOW]
  How long such a `jwt` stays valid is **[not stated]**. Storing those URLs inside cairn
  pages would carry a bearer link to private media.

---

## N. Organisation controls and repository visibility (customer side)

What the repository owner or an organisation owner must do before the connector works:

1. Create the token: fine-grained, resource owner = the repository's owner, repository access =
   only this repository, permissions Pull requests, Issues, Contents (read), and Metadata
   (read) if it is not added automatically (B.1: not stated as a rule). [doc: PAT-MANAGE]
2. If the owner is an organisation that requires approval (the default), an organisation owner
   approves the request; until then the token reads public data only. [doc: PAT-MANAGE,
   PAT-ORG-POLICY]
3. If the organisation sets a maximum token lifetime, the token's expiry must fit it, or calls
   are rejected. [doc: PAT-ORG-POLICY]
4. SAML SSO: nothing extra for fine-grained tokens ("authorized during token creation"); a
   classic token must be authorised per organisation. [doc: AUTH-REST, SAML-PAT]
5. IP allow list (GitHub Enterprise Cloud only): "Only organizations that use GitHub Enterprise
   Cloud can use IP allow lists." It applies to personal access tokens and to GitHub App
   tokens, except "Excluding installation tokens used by a GitHub App which is installed on a
   user account." [doc: IP-ALLOW] The server's egress address must be on the list. The status
   code and body of an allow-list refusal are **[not stated]**.

What an owner can switch off later: restrict PAT access to the organisation entirely (B.5),
revoke an approved token (PAT-ORG-REQUESTS), set or shorten the maximum lifetime, enable an
IP allow list, archive the repository (whether API reads continue is **[not stated]**,
L.4), make the repository private (a token that relied only on public access would then,
by B.6 and TROUBLESHOOTING, get 404 or 403; not stated for this case).

---

## O. Other token kinds and other hosts (deltas only)

### O.1 Classic token

Works for outside collaborators where fine-grained tokens do not (B.1); needs `repo`, which
grants write as well (B.3); not subject to organisation approval; must be SSO-authorised; can
be blocked by an organisation policy (403, B.6); has no organisation lifetime requirement;
same 5,000/hour user budget (J.1).

### O.2 GitHub App installation token

Expires after 1 hour, so the connector must hold the App's private key and mint tokens (B.4);
permissions as for fine-grained tokens; automatically SSO-authorised; its own rate budget,
at least 5,000 per hour, growing to at most 12,500, or 15,000 on a GitHub Enterprise Cloud
organisation (J.1); not exempt from IP allow lists unless installed on a user
account (N.5). Organisation installation needs an owner's approval of the App (pages on App
installation were not fetched: **[not stated]** here).

### O.3 GitHub Enterprise Server

- Base URL `http(s)://HOSTNAME/api/v3`. [doc: GHES-GETTING-STARTED]
- Same two API versions supported on 3.22 (`2026-03-10`, `2022-11-28`). [doc:
  GHES-API-VERSIONS] "GitHub Enterprise Server versions are decoupled from REST API versions."
  "The GitHub Enterprise Server release notes will state when a REST API version is no longer
  supported." [doc: GHES-API-VERSIONS] Older GHES releases may not offer `2026-03-10`
  (implied; **[not stated]** per release).
- "Rate limits are disabled by default for GitHub Enterprise Server. Contact your site
  administrator to confirm the rate limits for your instance." [doc: GHES-RATE-LIMITS]
- Search, permissions and field sets per GHES release were not compared: **[not stated]**
  here. GHES has its own description file in the spec repository (not read).
- GitHub Enterprise Cloud with data residency (`*.ghe.com`) uses another host; its docs were
  not fetched (GETTING-STARTED's CLI section shows `octocorp.ghe.com` as an example hostname).
  [example: GHES-GETTING-STARTED]

---

## P. Terms (quoted, not interpreted)

GitHub Terms of Service, "Effective date: April 27, 2026" [doc: TERMS]:

- H. API Terms: "Abuse or excessively frequent requests to GitHub via the API may result in the
  temporary or permanent suspension of your Account's access to the API."
- "You may not share API tokens to exceed GitHub's rate limitations."
- "You may not use the API to download data or Content from GitHub for spamming purposes,
  including for the purposes of selling GitHub users' personal information, such as to
  recruiters, headhunters, and job boards."
- "All use of the GitHub API is subject to these Terms of Service and the GitHub Privacy
  Statement" (link text).
- D (user-generated content): "Your Content that you post publicly, including issues, comments,
  and contributions to other Users' repositories, may be viewed by others."
- D.8, Public Repositories and Lawful Access: "Unless specifically set forth herein, these Terms
  do not restrict lawful access to or use of the contents of public repositories by third
  parties, or by GitHub or its Affiliates."
- D.9, Access Reciprocity: concerns automated access to public Content for "developing or
  training any commercially available artificial intelligence model, machine learning system,
  or similar technology"; it "does not apply to Access solely for the purpose of academic
  research or if, on the date you Access the Content, the number of monthly active users of the
  products or services made available by you is less than 700 million in the preceding
  calendar month."
- E.2: "GitHub considers the contents of private repositories to be confidential to you."
- J (AI Features) governs GitHub's own AI features and the use of their Inputs and Outputs;
  "This license does not extend to Your Content stored in repositories that are not provided
  as Input to an AI Feature." Nothing in J addresses a third party feeding API data to its own
  models.

Acceptable Use Policies [doc: AUP]:

- 6: "You will not reproduce, duplicate, copy, sell, resell or exploit any portion of the
  Service, use of the Service, or access to the Service without our express written
  permission."
- 7: "Scraping refers to extracting information from our Service via an automated process, such
  as a bot or webcrawler. Scraping does not refer to the collection of information through our
  API." "You may not use information from the Service (whether scraped, collected through our
  API, or obtained otherwise) for spamming purposes, including for the purposes of sending
  unsolicited emails to users or selling personal information, such as to recruiters,
  headhunters, and job boards." Permitted uses listed there: researchers ("public, non-personal
  information") and archivists ("public information").
- 8: "If you collect any personal information from the Service, you agree that you will only
  use that personal information for the purpose for which that User has authorized it. You
  agree that you will reasonably secure any personal information you have gathered from the
  Service, and you will respond promptly to complaints, removal requests, and "do not contact"
  requests from us or other users." (inner quotes are the page's)

Not found in the pages read: any statement about storing other people's issue and comment text
in a third-party system, or about using content read through the API as input to AI models
(other than D.9 above). The Privacy Statement's opening and third-party sections, and a
keyword search of it (API, AI, train, third part), added nothing on API use or on AI use of
data read through the API (its AI mentions concern GitHub's own processing of Personal Data). The Additional Product Terms and the Customer Agreement were not
fetched. **[not stated]**.

---

## Open questions for the connector

Most important first.

1. QUESTION: Does merging a pull request, or closing an issue, move its `updated_at`? Routes 1
   and 2 of section G depend on it, and nothing states it. (C.1, E.1, G)
2. QUESTION: Is `since` on the issue list (and on comment lists) inclusive or exclusive, and is
   `updated_at` second-precision only? An exclusive bound with second precision can drop items
   updated in the same second as the last poll. (E.1, A.5)
3. QUESTION: In what order does `GET /releases` return items, and does `published_at` change
   when a release is unpublished and published again? Without an order, finding releases published
   after T needs the whole list on every poll. (F.1)
4. QUESTION: Can a fine-grained token limited to one private repository use search on it
   ("does not require any permissions" versus the 422 for inaccessible `repo:`)? How fresh is
   the search index, what time zone does a date-only qualifier use, and does `>=` include the
   bound (CONFLICT in H)? (H)
5. QUESTION: Under 2026-03-10, with the beta `pull_request` leftovers deprecated, does a plain
   issue carry `pull_request: null`, no key, or nothing changes? Is `pull_request.merged_at`
   on an issue-list item absent or `null` for an unmerged PR (`nullable: false`, not required), and does the issue list return
   pull requests to a token without the Pull requests permission? (A.3, B.1, E.1)
6. QUESTION: Is `closed_by` filled in the issue list, or only on `GET /issues/{n}`? Same for
   PR `merged_by`, which is certainly absent from the list. (C.1, E.1)
7. QUESTION: Do the issue list, comment lists, reviews and releases answer `304` to
   `if-none-match` (SPEC lists 304 only for the PR list and GET, issue GET, search and
   `/rate_limit`)? This decides whether frequent polling is free. (I.3)
8. QUESTION: How does the connector tell apart, from the API alone, a token pending
   organisation approval (reads public only), a token revoked or blocked by lifetime policy, a
   missing permission, and a rate limit, given that all can surface as 403 or 404 with
   unstated messages? (B.5, B.6, J.4, K)
9. QUESTION: Review `state` spellings in the REST list: upper case per examples, lower case
   per the timeline event (CONFLICT), no enum; is `COMMENTED` a value; are other users'
   `PENDING` reviews visible; is an empty review `body` an empty string? (D.1)
10. QUESTION: What do deleted authors look like (`user: null` or a `ghost` user), and how are
    bots marked (`type: Bot`? not enumerated in Simple User)? Logins are reusable after 90 days,
    so is the numeric `id` the identity to keep? (L.3)
11. QUESTION: What happens to items the connector already ingested: a transferred issue (301 or
    404), a deleted issue (410 or 404), an issue converted to a discussion, a PR archived by a
    moderator (404 to non-triage users), a deleted comment or release? None of these has a
    stated list-level signal. (E.2, E.3, L)
12. QUESTION: Is the repository-wide `GET /issues/events` complete over the repository's life,
    in which order, and does a `merged` event's `actor` equal `merged_by`? (E.5)
13. QUESTION: Status codes and bodies for an IP allow-list refusal, a missing User-Agent, a
    10-second timeout, and rate-limit refusals (no 429 in SPEC). (A.2, A.4, J.4, N)
14. QUESTION: What does search return past the 1,000th result (empty page, 422)? (H)
15. QUESTION: Maximum length of issue, PR, comment, review and release bodies (no `maxLength`
    in SPEC). (M.1)
16. QUESTION: Is Metadata (read) always added to a fine-grained token, as the pre-fill example
    implies? (B.1)
17. QUESTION: Does the API return the body of a hidden (minimized) comment, and should the
    connector ingest it? Should `private-user-images` URLs with `jwt` be kept in stored text?
    (L.2, M.2)
