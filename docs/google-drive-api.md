# Google Drive API v3: reading one folder's documents as text, and noticing when they change

Researched 2026-11-13. No Drive API was called. Every page below was fetched that day with a
plain HTTPS GET (`?hl=en`, User-Agent `curl/8`), saved under `drive/raw/`, converted to text
under `drive/txt/` and read in full unless a note says otherwise. Code samples in Java, Python,
Node.js, PHP, .NET and Apps Script were skipped; cURL and raw HTTP samples were read. Final URLs
are given after redirects, with each page's "Last updated" line. The token side (service account
with domain-wide delegation, one user's refresh token, the token endpoint's errors, the general
error envelope) is in google-auth.md and is not repeated here.

Reference (base https://developers.google.com/workspace/drive/api/reference/rest/v3):

- **REST** = .../rest/v3 (2026-04-23)
- **FILES** = .../v3/files (2026-07-14); **F-LIST** = .../files/list (2026-07-07); **F-GET** =
  .../files/get (2026-03-20); **F-EXPORT** = .../files/export (2025-08-26); **F-DOWNLOAD** =
  .../files/download (2025-08-26)
- **CH** = .../v3/changes (2025-04-18); **CH-LIST** = .../changes/list (2026-07-07);
  **CH-START** = .../changes/getStartPageToken (2025-04-18)
- **REV** = .../v3/revisions (2025-07-23); **REV-LIST** = .../revisions/list (2026-07-07);
  **REV-GET** = .../revisions/get (2025-08-13)
- **ABOUT** = .../v3/about (2025-07-23); **ABOUT-GET** = .../about/get (2025-04-18)
- **DRIVES** = .../v3/drives (2025-07-16; field table read, method list skimmed); **DRIVES-GET**
  = .../drives/get (2025-08-13); **DRIVES-LIST** = .../drives/list (2026-07-07)
- **USER** = .../v3/User (2025-07-23)
- **OPS** = .../v3/operations (2026-09-14); **OPS-GET** = .../operations/get (2025-04-18)
- Fetched, skimmed only: **PERMS** = .../v3/permissions (2026-04-21). Fetched, not read:
  .../files/listLabels (2025-08-26), .../changes/watch (2026-07-07).
- **DISCOVERY** = https://www.googleapis.com/discovery/v1/apis/drive/v3/rest (listed on REST;
  `revision` 20261109). Used to check scopes, parameter defaults and bounds, field formats.

Guides (base https://developers.google.com/workspace/drive/api/guides):

- **G-OVERVIEW** = .../about-sdk (2026-09-25)
- **G-SCOPES** = .../api-specific-auth (2026-09-03). Also reached from .../about-auth, which
  redirects here.
- **G-SEARCH** = .../search-files (2026-10-12); **G-TERMS** = .../ref-search-terms (2026-10-12)
- **G-FOLDER** = .../folder (2026-09-03); **G-ABOUTFILES** = .../about-files (2026-09-03)
- **G-FILEMETA** = .../file-metadata (asked for as .../file; 2026-09-03)
- **G-FIELDS** = .../fields-parameter (2026-09-03); **G-PERF** = .../performance (2026-09-03)
- **G-DOWNLOADS** = .../manage-downloads (2026-09-03); **G-EXPORT** = .../ref-export-formats
  (2026-09-03); **G-MIME** = .../mime-types (2026-11-11); **G-LRO** = .../long-running-operations
  (2026-09-03)
- **G-CHANGES** = .../manage-changes (2026-11-07); **G-CHANGEOV** = .../change-overview
  (2026-09-03); **G-ABOUTCHANGES** = .../about-changes (2026-11-07); **G-CHANGELOGS** =
  .../about-changelogs (2026-11-07); **G-REVISIONS** = .../manage-revisions (2026-09-03)
- **G-PUSH** = .../push (2026-11-07)
- **G-SHORTCUTS** = .../shortcuts (2026-09-03). .../ref-single-parent (the old single-parent
  page) redirects here.
- **G-ABOUTSD** = .../about-shareddrives (2026-09-03); **G-ENABLESD** = .../enable-shareddrives
  (2026-09-03); **G-MANAGESD** = .../manage-shareddrives (2026-09-03); **G-SDDIFFS** =
  .../shared-drives-diffs (2026-09-03)
- **G-ROLES** = .../ref-roles (2026-09-03). Its two permission tables are drawn with icons; read
  from the HTML's `compare-yes` / `compare-no` classes.
- **G-SHARING** = .../manage-sharing (2026-09-14); **G-LIMITED** = .../limited-expansive-access
  (2026-09-03); **G-CONTENT** = .../content-restrictions (2026-09-03); **G-RESKEYS** =
  .../resource-keys (2026-09-03); **G-DELETE** = .../delete (2026-09-03); **G-USERINFO** =
  .../user-info (2026-09-03)
- **G-LIMITS** = .../limits (2026-10-30); **G-ERRORS** = .../handle-errors (page title "Resolve
  errors"; 2026-09-03)
- **G-MCP** = .../configure-mcp-server (2026-11-05; read: intro, consent-screen scopes,
  "Troubleshooting", the prompt-injection note); **G-MCP-ELIG** =
  .../drive-mcp-server-file-eligibility (2026-11-13)
- **G-EVENTS** = .../events-overview (2026-11-09; read to "Create a Drive subscription", the rest
  not read). Fetched, not read: .../search-shareddrives (2026-09-03).
- **G-TROUBLEAUTH** = https://developers.google.com/workspace/drive/api/troubleshoot-authentication-authorization
  (2026-09-03; linked from G-ERRORS; read in full, nothing beyond google-auth.md used)

Other Google pages:

- **DOCS-AUTH** = https://developers.google.com/workspace/docs/api/auth (2026-09-03). The Docs
  API's reference (`documents.get`) was not fetched.
- **SCOPES-LIST** = https://developers.google.com/identity/protocols/oauth2/scopes (2026-09-14;
  searched for the `drive` and `documents` rows only; it has no class column).
- **WS-CONSENT** = https://developers.google.com/workspace/guides/configure-oauth-consent
  (2026-09-03; read to "Scope categories").
- **WS-CREDS** = https://developers.google.com/workspace/guides/create-credentials (2026-10-12;
  read: intro, "Service account credentials" to the end; OAuth client sections skimmed).
- **WS-POLICY** = https://developers.google.com/workspace/workspace-api-user-data-developer-policy
  (2026-09-03)
- **UD-POLICY** = https://developers.google.com/terms/api-services-user-data-policy ("Last
  updated February 15, 2024"; read: "Additional Requirements for Specific API Scopes" and the
  sections around it)
- **DRIVE-TERMS** = https://developers.google.com/workspace/drive/api/terms (asked for as
  developers.google.com/drive/api/terms; 2026-09-03)
- **RESTRICTED-VERIF** = https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
  (2026-11-10)
- **SENSITIVE-VERIF** = .../production-readiness/sensitive-scope-verification (2026-11-10; only
  its "Exceptions to verification requirements" read; the same text as RESTRICTED-VERIF's)
- **VERIF-EXC** = https://support.google.com/cloud/answer/13464323 ("When is verification not
  needed"; no date shown)
- **RESTRICTED-LIST** = https://support.google.com/cloud/answer/13464325 ("Restricted Scopes";
  no date shown)
- Fetched, nothing used: https://support.google.com/cloud/answer/9110914 (the "OAuth Application
  Verification FAQ" link on WS-POLICY), which redirected to the OAuth App Verification Help
  Center home (support.google.com/cloud/answer/13463073), an index page.
- **TOOLS-SAFETY** = https://developers.google.com/workspace/tools-safety ("Google Workspace
  standardized model for agent tools and APIs"; 2026-09-03)
- **BLOG-AGENT** = https://workspaceupdates.googleblog.com/2026/05/agent-tools-and-security-updates-for-workspace-developers.html
  (dated May 1, 2026; linked from G-LIMITS; asked for over http, redirected to https; read in
  full, nothing used beyond what G-LIMITS and TOOLS-SAFETY say)
- **ADMIN-APPS** = https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data
  (asked for as support.google.com/a/answer/7281227; 2026-11-10)
- **ADMIN-SHARING** = https://knowledge.workspace.google.com/admin/drive/manage-external-sharing-for-your-organization
  (asked for as support.google.com/a/answer/60781; 2026-11-10; read: intro, "Turn on or off
  external sharing", "Understand indicator display behavior")
- **ADMIN-IRM** = https://knowledge.workspace.google.com/admin/security/dlp-disable-download-print-copy
  (2026-11-10)
- The Admin Help page on domain-wide delegation is cited, in our words, in google-auth.md (its
  "DWD").

Not fetched (404 on guessed URLs): `.../guides/manage-content-restrictions` (the page is
`.../guides/content-restrictions`, fetched above) and `.../guides/ref-labels`.

In all: 78 pages plus the discovery document.

Tags: **[doc: X]** = stated on page X; **[example: X]** = visible only in an example on page X;
**[discovery]** = from the discovery document; **[not stated]** = nowhere in the pages above.
**QUESTION:** marks an open point that matters for the design; **CONFLICT** quotes two Google
statements that disagree.

---

## Headline points (read first)

1. **There is no call for the changes in one folder.** `changes.list` reads a whole user's or a
   whole shared drive's change log; none of its parameters names a folder [doc: CH-LIST;
   DISCOVERY]. `'<id>' in parents` "Only checks direct parent folders; doesn't match indirect
   ancestor folders." [doc: G-TERMS], and "The Drive API doesn't provide a query operator or
   parameter to recursively search descendant subfolder trees in a single API call." [doc:
   G-SEARCH]. A folder tree costs one `files.list` per folder per poll.
2. **A Google Doc has no content hash and no head revision ID.** `md5Checksum`, `sha256Checksum`
   and `headRevisionId` are for "files with binary content" only, and the SHA checksums are
   "not populated for Docs Editors or shortcut files" [doc: FILES]. What is left is
   `modifiedTime` ("The last time the file was modified by anyone") and `version` ("A
   monotonically increasing version number for the file. This reflects every change made to the
   file on the server, even those not visible to the user.") [doc: FILES]. Neither is described
   as content-only. QUESTION below.
3. **A reader cannot see revisions.** "To access the revision history, a user must have the
   `role` of `owner`, `organizer`, `fileOrganizer`, or `writer`." [doc: G-REVISIONS]; G-ROLES
   gives "Access historical revisions" to writer and above, not to commenter or reader [doc:
   G-ROLES, read from the icons]. Even then, for Docs the list "might be incomplete" and
   revisions "may be merged together" [doc: REV-LIST, G-CHANGEOV].
4. **A Doc exports as Markdown or plain text, at most 10 MB.** The export table lists
   `text/markdown` (.md) and `text/plain` (.txt) for Documents; `text/plain` for Presentations;
   Sheets only as CSV/TSV of the first sheet ("first-sheet only") [doc: G-EXPORT]. "Note that the
   exported content is limited to 10 MB." [doc: F-EXPORT]. The error when it is exceeded is
   **[not stated]**.
5. **Every scope whose stated purpose covers ordinary Drive files, and whose class is stated, is
   Restricted**, except `drive.file` (the others `files.list` accepts are `drive.appdata`, the
   app's own hidden folder, and `drive.photos.readonly`, class not stated). `drive.file` covers
   only files "that you open with an app or that the user shares with
   an app while using the Google Picker API or the app's file picker" [doc: G-SCOPES]. The
   narrowest scope that can export is `drive.readonly` (section B). The internal-use exemptions
   are quoted in section M.
6. **`files.list` returns trashed files by default, in no defined order, with almost no
   fields.** "This method returns all files by default, including trashed files." [doc: F-LIST];
   "If you omit the `orderBy` query parameter, there's no default sort order and the items are
   returned arbitrarily." [doc: G-SEARCH]; without `fields` only `kind`, `id`, `name`, `mimeType`
   (and maybe `resourceKey`, CONFLICT in D) come back.
7. **Quota refusals come as 403 or 429 with the old `error.errors[]` body.** "If you exceed a
   quota, you'll receive a `403: User rate limit exceeded` HTTP status code response. Additional
   rate limit checks on the Drive backend might also generate a `429: Rate limit exceeded`
   response." [doc: G-LIMITS]. The reasons `downloadQuotaExceeded`, `exportSizeLimitExceeded` and
   `cannotDownloadFile` appear on **no** page fetched **[not stated]** (section K).
8. **Sharing with the whole domain does not reach a service account.** "Note that service
   accounts do not belong to your Google Workspace domain, unlike user accounts. If you share
   Google Workspace assets, like documents or events, with your entire Google Workspace domain,
   they're not shared with service accounts." [doc: G-MANAGESD]. The folder has to be shared with
   the service account's address, or the shared drive has to add it as a member (section C).

---

## A. Service, version, base URL, time format

- Service `googleapis.com/drive/v3`; service endpoint `https://www.googleapis.com`; discovery
  document `https://www.googleapis.com/discovery/v1/apis/drive/v3/rest` [doc: REST]. DISCOVERY:
  `rootUrl` `https://www.googleapis.com/`, `servicePath` `drive/v3/`, `batchPath`
  `batch/drive/v3`.
- Methods used here, all `GET` except `files.download` [doc: REST]:
  - `GET /drive/v3/files` (`files.list`), `GET /drive/v3/files/{fileId}` (`files.get`),
    `GET /drive/v3/files/{fileId}/export` (`files.export`);
  - `GET /drive/v3/changes/startPageToken`, `GET /drive/v3/changes`;
  - `GET /drive/v3/files/{fileId}/revisions`, `GET /drive/v3/files/{fileId}/revisions/{revisionId}`;
  - `GET /drive/v3/about`, `GET /drive/v3/drives/{driveId}`;
  - `POST /drive/v3/files/{fileId}/download` (`files.download`, a long-running operation that
    creates nothing in Drive but is a POST; see F.5) and `GET /drive/v3/operations/{name}`.
- "The URL uses gRPC Transcoding syntax." [doc: every method page]
- Global query parameters [discovery]: `alt`, `fields`, `prettyPrint`, `quotaUser`, `key`,
  `access_token`, `oauth_token`, `callback`, `$.xgafv`, `uploadType`, `upload_protocol`.
- **Times.** Every time field on File, Change and Revision is a string, described as "RFC 3339
  date-time" [doc: FILES, CH, REV]; DISCOVERY types them `string`, `format: date-time`. No
  response example on any page fetched shows a timestamp, so whether Drive emits `Z` or an
  offset, and how many fractional digits, is **[not stated]**.
- **Times in `q`:** "RFC 3339 format, default time zone is UTC, such as
  `2012-06-04T12:00:00-08:00`." [doc: G-TERMS, `modifiedTime`]. G-SEARCH's examples use quoted
  values without a zone: `modifiedTime > '2012-06-04T12:00:00'` and "Files modified after a given
  date (default time zone is UTC)". Whether fractional seconds or `Z` are accepted in `q` is
  **[not stated]**.
- `int64` values (`version`, `size`, `quotaBytesUsed`) are JSON strings: "`string (int64
  format)`" [doc: FILES; DISCOVERY `format: int64`].
- **All-day** does not apply: Drive items carry instants (`createdTime`, `modifiedTime`, change
  `time`, revision `modifiedTime`), never dates without a time. Created = `createdTime`; last
  changed = `modifiedTime`; trashed = `trashedTime` (shared drives only); a permanent deletion
  has no timestamp beyond the change entry's `time` (G.2).

## B. Scopes, their class, and which one can serve this connector

**Which scopes each method accepts** [doc: method pages; DISCOVERY agrees]:

| Method | Scopes accepted |
|---|---|
| `files.list`, `files.get`, `changes.list`, `changes.getStartPageToken`, `revisions.list`, `revisions.get` | `drive`, `drive.appdata`, `drive.file`, `drive.meet.readonly`, `drive.metadata`, `drive.metadata.readonly`, `drive.photos.readonly`, `drive.readonly` |
| `files.export` | `drive`, `drive.file`, `drive.meet.readonly`, `drive.readonly` |
| `files.download` | `drive`, `drive.file`, `drive.readonly` |
| `about.get` | `drive`, `drive.appdata`, `drive.file`, `drive.metadata`, `drive.metadata.readonly`, `drive.photos.readonly`, `drive.readonly` |
| `drives.get`, `drives.list` | `drive`, `drive.readonly` |

(all prefixed `https://www.googleapis.com/auth/`)

**Class, from the provider's own tables:**

| Scope | Class | Description (quoted) | Source |
|---|---|---|---|
| `drive.file` | Non-sensitive | "Create new Drive files, or modify existing files, that you open with an app or that the user shares with an app while using the Google Picker API or the app's file picker." | G-SCOPES ("Non-sensitive scopes"); DOCS-AUTH: Usage column "Recommended", class Non-sensitive |
| `drive.appdata` | Non-sensitive | "View and manage the app's own configuration data in your Google Drive." | G-SCOPES |
| `drive.apps.readonly` | Sensitive | "View apps authorized to access your Drive." | G-SCOPES |
| `drive.readonly` | Restricted | "View and download all your Drive files." | G-SCOPES; RESTRICTED-LIST; DOCS-AUTH "Restricted" |
| `drive.metadata.readonly` | Restricted | "View metadata for files in your Drive." | G-SCOPES; RESTRICTED-LIST |
| `drive.metadata` | Restricted | "View and manage metadata of files in your Drive." | G-SCOPES; RESTRICTED-LIST |
| `drive.meet.readonly` | Restricted | "View Drive files created or edited by Google Meet." | G-SCOPES; RESTRICTED-LIST |
| `drive.activity.readonly` | Restricted | "View the activity record of files in your Drive." | G-SCOPES; RESTRICTED-LIST |
| `drive` | Restricted | "View and manage all your Drive files." | G-SCOPES; RESTRICTED-LIST |
| `documents.readonly` (Docs API) | Sensitive | "See all your Google Docs documents." | DOCS-AUTH |
| `drive.photos.readonly` | **[not stated]** in G-SCOPES or RESTRICTED-LIST | "View the photos, videos and albums in your Google Photos" | SCOPES-LIST |

- The class definitions: "Non-sensitive: These scopes provide the smallest scope of authorization
  and only require basic OAuth App Verification."; "Sensitive: ... They require additional OAuth
  App Verification."; "Restricted: These scopes provide wide access to Google user data and
  require restricted scope OAuth App Verification." [doc: G-SCOPES]
- "If you store restricted scope data on servers (or transmit), then you must go through a
  security assessment." [doc: G-SCOPES, DOCS-AUTH]
- WS-POLICY defines Restricted Drive scopes as any that permit an app to "Read, modify, or manage
  the content or metadata of a user's Drive files, without the user individually granting
  file-by-file access." [doc: WS-POLICY]
- Admins can block the "high-risk" scopes; the Drive & Docs list includes `documents.readonly`,
  `drive.readonly`, `drive.metadata.readonly`, `drive.meet.readonly` and `drive.photos.readonly`,
  and does not include `drive.file` [doc: ADMIN-APPS]. See L.
- Scope descriptions differ between pages (G-SCOPES "View and download all your Drive files." vs
  DISCOVERY and SCOPES-LIST "See and download all your Google Drive files"). Wording only.

**Which scope can export the Docs of a folder the server did not create:**

- `files.export` accepts only `drive`, `drive.file`, `drive.meet.readonly`, `drive.readonly`
  [doc: F-EXPORT].
- `drive.meet.readonly` is limited to files "created or edited by Google Meet" [doc: G-SCOPES].
- `drive.file` covers files the app opened or the user shared with the app "while using the
  Google Picker API or the app's file picker" [doc: G-SCOPES]. G-SCOPES also says "The
  `drive.file` scope works with all Drive API REST Resources". DOCS-AUTH describes it as "See,
  edit, create, and delete only the specific Google Drive files you use with this app." Whether `drive.file` covers a
  folder (and its children) shared with a service account's address in the Drive UI, with no
  Picker involved, is **[not stated]**. QUESTION below.
- So the narrowest scope the pages establish for exporting any Doc in a folder this app did not
  create is `drive.readonly` (Restricted). `drive.metadata.readonly` cannot download: "an app
  using the `drive.readonly.metadata` scope isn't authorized to download the file contents" [doc:
  G-DOWNLOADS; the page spells the scope `drive.readonly.metadata`, a name that appears in no
  scope list]; "Both strictly prohibit access to file content." [doc: G-FILEMETA, of `drive.metadata`
  and `drive.metadata.readonly`].
- The Docs API (`documents.readonly`, Sensitive) can read a Doc's content, but listing the folder
  still needs a Drive scope from the table above. How `documents.get` behaves was not researched
  **[not stated]** here.
- `drive.readonly` is also what the Drive MCP server asks for, together with `drive.file` [doc:
  G-MCP].

**Restricted-scope qualification** [doc: G-SCOPES]: "Only specific application types are
permitted to use restricted scopes for Google Drive." The three categories are "Backup and sync",
"Productivity and education" ("Apps with a primary user interface that might involve interaction
with Drive files, metadata, or permissions. These apps include task management, note-taking,
workgroup communications, and classroom collaboration apps.") and "Reporting and security".
DRIVE-TERMS and WS-POLICY give the same three. Section M has the exemptions.

## C. Who must grant what: delegation, sharing with the service account, one person's token

Token mechanics are in google-auth.md (A: service account and JWT; B: refresh token).

**C.1 Service account with domain-wide delegation, impersonating a person.**

- Admin: Security > Access and data control > API controls > "Manage Domain Wide Delegation",
  "Add new", the service account's client ID and "a comma-delimited list of the scopes", then
  "Authorize". "Changes can take up to 24 hours but typically happen more quickly." [doc:
  WS-CREDS; same as google-auth.md A.2]. For this connector the scope list would be
  `https://www.googleapis.com/auth/drive.readonly` (B).
- "Use domain-wide delegation when your application needs to access Google Workspace data on
  behalf of multiple individual users in your organization" [doc: WS-CREDS].
- The connector then sees what the impersonated person sees: their My Drive, "Shared with me",
  and the shared drives they belong to (corpora, D.3). The named folder must be visible to that
  person.

**C.2 Service account the folder is shared with directly (no delegation).**

- "If your app only needs to read or write specific files (such as a Google Sheet or a Google
  Drive folder), you don't need to assign administrative roles or configure domain-wide
  delegation. Instead, you can directly share individual files with the service account's email
  address using the standard UI." [doc: WS-CREDS]
- Steps: copy the address (e.g. `my-service-account@my-project.iam.gserviceaccount.com`), Share,
  add it "and assign the appropriate access level (such as Editor or Viewer)", deselect Notify
  people ("since service accounts don't have inboxes it won't receive the invitation email, but
  the permission is still granted") [doc: WS-CREDS].
- "Note that Identity and Access Management (IAM) roles configured in the Google Cloud Console
  don't grant access to Google Workspace assets" [doc: WS-CREDS].
- Domain-wide sharing does not count (headline 8) [doc: G-MANAGESD].
- Who may share: in My Drive `owner` or `writer` ("Requires `owner` if `writersCanShare=false`");
  in a shared drive a file needs `organizer`, `fileOrganizer` or `writer`, a folder `organizer`
  ("`fileOrganizer` can also share if `sharingFoldersRequiresOrganizerPermission` is `false`"),
  membership `organizer` [doc: G-SHARING].
- A shared drive can instead add the service account as a member; members "have access to all
  files within the shared drive" [doc: G-ABOUTSD]. That a service account can be a shared-drive
  member is implied by "To make permissions available to users, groups, and service accounts,
  you assign roles." [doc: G-ROLES]; a page that says so for shared-drive membership is
  **[not stated]**.
- Whether an admin's setting that turns external sharing off blocks sharing a folder with a service
  account (which is not in the domain) is **[not stated]**. ADMIN-SHARING says only
  (paraphrased) that the indicator always shows when a service account can reach the file,
  whether the service account belongs to someone inside the organization or outside it.
  QUESTION below.
- Service accounts "don't have storage quota and can't own files" [doc: G-ABOUTSD, G-ERRORS].
  Not a concern for a reader.

**C.3 One person's refresh token.** Same visibility as C.1 (that person's files), with the
refresh-token failure modes of google-auth.md B.4.

**C.4 Admin-side switches** (what can stop each of these), from ADMIN-APPS:

- Restricting the Drive service (paraphrased): once access is Restricted, apps installed before
  that are not trusted stop working and their tokens are revoked; restricting Drive restricts the
  Google Forms API too.
- An app can be set "Trusted", "Specific Google data", "Limited" or "Blocked"; a Blocked app
  reaches no Google data (paraphrased).
- High-risk Drive scopes (B) can be restricted separately.
- "Internal apps": a setting that lets apps the organization built itself reach restricted
  Google Workspace APIs (paraphrased), with a "Trust internal apps" box.
- How these settings apply to a service account with delegation (as opposed to an OAuth client
  ID) is **[not stated]** on ADMIN-APPS. G-MCP says, for the MCP server only, that for such
  service accounts "access is governed by the domain-wide delegation settings that your Google
  Workspace administrator manages."
- DLP IRM rules can stop users from downloading, printing or copying files in Google Drive
  (paraphrased) [doc: ADMIN-IRM]; see F.6.

## D. `files.list` and the query language

`GET https://www.googleapis.com/drive/v3/files`; request body empty. [doc: F-LIST]

### D.1 Parameters

| Parameter | What the pages say |
|---|---|
| `q` | "A query for filtering the file results." Syntax in D.2. |
| `fields` | System parameter. Without it, defaults only (D.4). |
| `pageSize` | "The maximum number of files to return. The service may return fewer than this value." "If unspecified, at most 100 files will be returned for shared drives, and the entire list of files for non-shared drives." "The maximum value is 1000; values above 1000 will be coerced to 1000." [doc: F-LIST]. DISCOVERY: `int32`, `default` 100, `minimum` 1, `maximum` 1000. **CONFLICT:** "the entire list of files for non-shared drives" (F-LIST) vs `"default": "100"` (DISCOVERY). |
| `pageToken` | "This should be set to the value of `nextPageToken` from the previous response." |
| `orderBy` | "A comma-separated list of sort keys." Keys: `createdTime`, `folder`, `modifiedByMeTime`, `modifiedTime`, `name`, `name_natural`, `quotaBytesUsed`, `recency`, `sharedWithMeTime`, `starred`, `viewedByMeTime`. "Each key sorts ascending by default, but can be reversed with the `desc` modifier. Example usage: `?orderBy=folder,modifiedTime desc,name`." |
| `corpora` | `user`, `domain`, `drive`, `allDrives`. "Prefer `user` or `drive` to `allDrives` for efficiency. By default, corpora is set to `user`. However, this can change depending on the filter set through the `q` parameter." |
| `driveId` | "ID of the shared drive to search." |
| `includeItemsFromAllDrives` | "Whether both My Drive and shared drive items should be included in results." DISCOVERY default `false`. |
| `supportsAllDrives` | "Whether the requesting application supports both My Drives and shared drives." DISCOVERY default `false`. |
| `spaces` | "A comma-separated list of spaces to query within the corpora. Supported values are `drive` and `appDataFolder`." DISCOVERY default `drive`. |
| `includeLabels`, `includePermissionsForView` | label IDs for `labelInfo`; only `published` |
| deprecated | `corpus`, `includeTeamDriveItems`, `supportsTeamDrives`, `teamDriveId` |

- **Ordering.** "If you omit the `orderBy` query parameter, there's no default sort order and the
  items are returned arbitrarily." [doc: G-SEARCH]. On `createdTime`: "Avoid using this key for
  queries on large item collections as it might result in timeouts or other issues. For
  time-related sorting on large item collections, use `modifiedTime desc` instead." [doc:
  F-LIST]; G-SEARCH: `modifiedTime` "it's optimized to handle these queries". Sorting with
  `fullText`: "Sorting is not supported for queries with fullText terms. Results are always in
  descending relevance order." [example: G-ERRORS, the `badRequest` body]. Ties within a key:
  **[not stated]**.
- **Trashed files are included** unless `q` says `trashed=false` [doc: F-LIST].

### D.2 The `q` language [doc: G-TERMS unless marked]

- Form: `query_term operator values`.
- Operators: `contains`, `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `and`, `or`, `not`, `has`.
- Terms that matter here:

| Term | Operators | Notes (quoted) |
|---|---|---|
| `name` | `contains`, `=`, `!=` | "Surround with single quotes (`'`). Escape single quotes in queries with `\'`". "The `contains` operator only performs prefix matching for a `name` term." |
| `mimeType` | `contains`, `=`, `!=` | quoted string |
| `modifiedTime` | `<=`, `<`, `=`, `!=`, `>`, `>=` | "RFC 3339 format, default time zone is UTC". "Fields of type `date` are not comparable to each other, only to constant dates." |
| `createdTime` | same | "Use RFC 3339 format, default time zone is UTC ... Supported in Drive API v3 only." |
| `trashed` | `=`, `!=` | "Can be either `true` or `false`." |
| `parents` | `in` | "Whether the `parents` collection contains the specified ID. Only checks direct parent folders; doesn't match indirect ancestor folders." |
| `owners`, `writers`, `readers` | `in` | e-mail addresses |
| `fullText` | `contains` | name, description, indexable text or content; whole tokens only |
| `shortcutDetails.targetId` | `=`, `!=` | "The ID of the item the shortcut points to." |
| `visibility`, `starred`, `sharedWithMe`, `viewedByMeTime`, `properties`, `appProperties` | | not needed here |

- `modifiedByMeTime` is a sort key but **not** a query term [doc: G-TERMS, F-LIST].
- Combining: `and`, `or`, `not`, parentheses: "`modifiedTime > '2012-06-04T12:00:00' and
  (mimeType contains 'image/' or mimeType contains 'video/')`" [example: G-SEARCH]. Lower-case
  `and` in every G-SEARCH example.
- Examples relevant here [example: G-SEARCH]: `'1234567' in parents and trashed = false`;
  `mimeType = 'application/vnd.google-apps.folder'`; `mimeType != 'application/vnd.google-apps.folder'`;
  `createdTime > '2023-01-01T00:00:00'`; `name = 'Project Plan' and trashed = false`.
- Escaping in names: "use a backslash to escape them: `name contains 'quinn\'s paper\\essay'`"
  [doc: G-SEARCH]. URL encoding: "`name = 'hello'` is encoded as `name+%3d+%27hello%27`" [doc:
  G-SEARCH].
- **CONFLICT on shortcut queries.** G-SHORTCUTS's example is
  `mimeType='application/vnd.google-apps.shortcut' AND shortcutDetails.targetMimeType='application/vnd.google-apps.spreadsheet'`
  (upper-case `AND`, term `shortcutDetails.targetMimeType`); G-TERMS ("The following table lists
  all valid file query terms") lists only `shortcutDetails.targetId`. QUESTION below.
- Labels: `'labels/LABEL_ID' in labels` [example: G-SEARCH]; not in the G-TERMS table.
- What a malformed `q` returns: a 400 `badRequest` is implied by the generic 400 entry ("The value
  supplied or a combination of provided fields is invalid.") [doc: G-ERRORS]; the exact body for
  a bad `q` is **[not stated]**.

### D.3 Corpora, shared drives, `incompleteSearch` [doc: G-ENABLESD, G-ABOUTFILES, F-LIST]

| `includeItemsFromAllDrives` | `corpora` | Query description (G-ENABLESD) |
|---|---|---|
| `true` | `user` | "Queries files that the user has accessed, including both shared drive and My Drive files." |
| `true` | `domain` | "Queries files that are shared to the domain, including both shared drive and My Drive files." |
| `true` | `drive` | "Queries all items in the specified shared drive. The `driveId` must be specified in the request." |
| `true` | `allDrives` | "Queries files that the user has accessed and all shared drives in which they're a member." |

- `includeItemsFromAllDrives`: "If not present or set to false, then shared drive items are not
  returned." `supportsAllDrives`: "If false, shared drive items are not included in the
  response." [doc: G-ENABLESD]
- `user` corpus: "all files created by and opened by the user in "My Drive", and those shared
  directly with the user in "Shared with me."" [doc: G-ABOUTFILES]
- `incompleteSearch`: "Whether the search process was incomplete. If true, then some search
  results might be missing, since all documents were not searched. This can occur when searching
  multiple drives with the `allDrives` corpora" [doc: F-LIST]
- A folder in a shared drive, listed with `'<id>' in parents`: G-SEARCH says for a public folder
  "If the folder is located within a shared drive, you must also set `supportsAllDrives=true` and
  `includeItemsFromAllDrives=true` on the request." Its recursive sample sets both flags and no
  `corpora` or `driveId` [example: G-SEARCH]. Whether `corpora=drive&driveId=...` is required, or
  the default `user` corpus finds a shared-drive folder's children, is **[not stated]**.
  QUESTION below.

### D.4 Response

```
{
  "files": [ { object (File) } ],
  "nextPageToken": string,
  "kind": string,
  "incompleteSearch": boolean
}
```

- `files[]`: "If `nextPageToken` is populated, then this list may be incomplete and an additional
  page of results should be fetched." [doc: F-LIST]
- `nextPageToken`: "This will be absent if the end of the files list has been reached. If the
  token is rejected for any reason, it should be discarded, and pagination should be restarted
  from the first page of results. The page token is typically valid for several hours. However,
  if new items are added or removed, your expected results might differ." [doc: F-LIST]. The
  error for a rejected token is **[not stated]**.
- `kind`: the fixed string `drive#fileList`.
- Whether an empty result has `files: []` or omits `files` is **[not stated]**; the samples use
  `response.get("files", [])` [example: G-SEARCH].
- **Default fields. CONFLICT.** G-SEARCH, G-FILEMETA and G-DELETE: "the `list` method returns
  only the `kind`, `id`, `name`, `mimeType`, and `resourceKey` fields for each file"; G-FIELDS and
  G-PERF: "the `list` method on the `files` resource only returns the `kind`, `id`, `name`, and
  `mimeType` fields". Either way, `modifiedTime`, `version`, `parents`, `webViewLink`,
  `trashed`, `capabilities` etc. must be asked for, e.g.
  `fields=nextPageToken,files(id,name,mimeType,modifiedTime,version,parents,trashed,webViewLink,capabilities/canDownload)`.
- `fields` syntax [doc: G-FIELDS]: comma list; `a/b` nests; `a(b,c)` sub-selects; `*` wildcard.
  An invalid selection gives `HTTP 400 Bad Request` ("Invalid field selection canAddChildren." in
  its example). Map keys with `/` or `.` cannot be selected: "`fields=exportLinks/application/pdf`
  yields an `HTTP 400 Bad Request` error"; ask for the whole `exportLinks`.
- `fields` is mandatory only for `about` (and `approvals`, `comments`, `replies`): "These methods
  don't return a default set of fields." [doc: G-FIELDS]; for `about.get`, "If you omit the
  parameter, the method returns an error." [doc: G-USERINFO]. G-PERF's version of the list omits
  `approvals`.

### D.5 Walking a folder tree [doc: G-SEARCH, "Search descendant folders and files recursively"]

"To search a folder and all its descendant subfolders and files, your application must perform
client-side tree traversal":

- "Query direct children of the target folder using `'FOLDER_ID' in parents and trashed = false`."
- "Identify subfolders by filtering results where `mimeType = 'application/vnd.google-apps.folder'`."
- "Recursively or iteratively query each child folder's ID to retrieve its contents."

The sample uses `fields: 'nextPageToken, files(id, name, mimeType)'`, `pageSize: 1000`,
`supportsAllDrives: true`, `includeItemsFromAllDrives: true` [example: G-SEARCH]. It does not
guard against cycles or shortcuts to folders (E.3). Folder depth is capped at 100 levels in My
Drive and in shared drives [doc: G-FOLDER, G-MANAGESD]; a folder holds at most 500,000 items
[doc: G-FOLDER].

`files.get`: `GET https://www.googleapis.com/drive/v3/files/{fileId}`; parameters
`supportsAllDrives`, `acknowledgeAbuse`, `includeLabels`, `includePermissionsForView`; returns a
File [doc: F-GET]. The folder the operator names can be checked with it (its `mimeType`,
`driveId`, `capabilities.canListChildren`).

## E. The File resource

[doc: FILES; types from DISCOVERY]

| Field | Type | Description (quoted, shortened where marked) |
|---|---|---|
| `id` | string | "The ID of the file." G-ABOUTFILES: "File IDs are stable throughout the life of the file, even if the file name changes." |
| `name` | string | "The name of the file. This isn't necessarily unique within a folder." |
| `mimeType` | string | "The MIME type of the file." |
| `description` | string | "A short description of the file." |
| `createdTime` | string, date-time | "The time at which the file was created (RFC 3339 date-time)." |
| `modifiedTime` | string, date-time | "The last time the file was modified by anyone (RFC 3339 date-time)." "Note that setting `modifiedTime` also updates `modifiedByMeTime` for the user." |
| `modifiedByMeTime` | string, date-time | "Output only. The last time the file was modified by the user (RFC 3339 date-time)." (the caller, so meaningless for a service account that never edits) |
| `version` | string, int64 | "Output only. A monotonically increasing version number for the file. This reflects every change made to the file on the server, even those not visible to the user." |
| `headRevisionId` | string | "Output only. The ID of the file's head revision. This is currently only available for files with binary content in Google Drive." |
| `md5Checksum` | string | "Output only. The MD5 checksum for the content of the file. This is only applicable to files with binary content in Google Drive." |
| `sha256Checksum` | string | "Output only. The SHA256 checksum associated with this file, if available. This field is only populated for files with content stored in Google Drive; it's not populated for Docs Editors or shortcut files." (`sha1Checksum` the same) |
| `size` | string, int64 | "Output only. Size in bytes of blobs and Google Workspace editor files. Won't be populated for files that have no size, like shortcuts and folders." |
| `trashed` | boolean | "Whether the file has been trashed, either explicitly or from a trashed parent folder. Only the owner may trash a file, but other users can still access the file in the owner's trash until it's permanently deleted." |
| `explicitlyTrashed` | boolean | "Output only. Whether the file has been explicitly trashed, as opposed to recursively trashed from a parent folder." |
| `trashedTime`, `trashingUser` | date-time, User | "Only populated for items in shared drives." |
| `parents[]` | string | "The ID of the parent folder containing the file. A file can only have one parent folder; specifying multiple parents isn't supported." |
| `driveId` | string | "Output only. ID of the shared drive the file resides in. Only populated for items in shared drives." |
| `owners[]` | User | "Output only. The owner of this file. Only certain legacy files may have more than one owner. This field isn't populated for items in shared drives." |
| `lastModifyingUser` | User | "Output only. The last user to modify the file. This field is only populated when the last modification was performed by a signed-in user." |
| `webViewLink` | string | "Output only. A link for opening the file in a relevant Google editor or viewer in a browser." G-RESKEYS: URL fields "also include the `resourceKey`". |
| `exportLinks` | map string to string | "Output only. Links for exporting Docs Editors files to specific formats." |
| `capabilities.canDownload` | boolean | "Output only. Whether the current user can download this file." |
| `capabilities.canListChildren` | boolean | "always `false` when the item isn't a folder" |
| `capabilities.canReadRevisions` | boolean | "Whether the current user can read the revisions resource of this file." |
| `capabilities.canAccessViaGenAi` | boolean | "Whether the current user can access this file via Gen AI features. For more information, see Drive MCP file eligibility." |
| `copyRequiresWriterPermission` | boolean | "Whether the options to copy, print, or download this file should be disabled for readers and commenters." |
| `downloadRestrictions` | DownloadRestrictionsMetadata | `itemDownloadRestriction`, `effectiveDownloadRestrictionWithContext`, each `{restrictedForReaders, restrictedForWriters}` (F.6) |
| `shortcutDetails` | object | `targetId`, `targetMimeType` (the target's MIME type as it was when the shortcut was created), `targetResourceKey` |
| `labelInfo.labels[]` | Label | "The set of labels on the file as requested by the label IDs in the `includeLabels` parameter. By default, no labels are returned." |
| `contentRestrictions[]` | ContentRestriction | "Restrictions for accessing the content of the file. Only populated if such a restriction exists." Fields `readOnly`, `reason`, `type` (only `globalContentRestriction`), `restrictingUser`, `restrictionTime`, `ownerRestricted`, `systemRestricted`. A content restriction "isn't an access restriction" [doc: G-CONTENT]; it does not stop reading. |
| `clientEncryptionDetails` | object | `encryptionState`: `encrypted` or `unencrypted` |
| `resourceKey` | string | "A key needed to access the item via a shared link." |
| `kind` | string | `drive#file` |

User [doc: USER]: `displayName`, `kind` (`drive#user`), `me`, `permissionId`, `emailAddress`
("This may not be present in certain contexts if the user has not made their email address
visible to the requester."), `photoLink`.

### E.1 MIME types [doc: G-MIME]

| MIME type | Description |
|---|---|
| `application/vnd.google-apps.document` | Google Docs |
| `application/vnd.google-apps.spreadsheet` | Google Sheets |
| `application/vnd.google-apps.presentation` | Google Slides |
| `application/vnd.google-apps.folder` | Google Drive folder |
| `application/vnd.google-apps.shortcut` | Google Drive shortcut |
| `application/vnd.google-apps.drive-sdk` | Third-party shortcut |
| `application/vnd.google-gsuite.encrypted` | Client-side encrypted file |

Also listed: audio, drawing, file, form, fusiontable, jam, mail-layout, map, photo, pic, project,
script, site, unknown, vid, video, `application/vnd.google-gemini.conversation`,
`application/vnd.google-gemini.gem`. Plain text, Markdown and PDF uploads are ordinary
("blob") files with their own MIME types (`text/plain`, `text/markdown`, `application/pdf` as
the export table names them); what MIME type Drive assigns to an uploaded `.md` file is
**[not stated]** ("Google Drive attempts to automatically detect an appropriate value from
uploaded content, if no value is provided." [doc: FILES]).

### E.2 Files with more than one parent. CONFLICT

- FILES, DISCOVERY and G-FOLDER: "A file can only have one parent folder; specifying multiple
  parents isn't supported."
- G-ABOUTFILES: "Note: A single file stored on My Drive can be in multiple folders. A single file
  stored on a shared drive can only have one parent folder."
- Shared drives: "Shared drive files must have exactly one parent." [doc: G-ABOUTSD]; "with the
  exception of the top level folder, the parents list must contain exactly one item if the file is
  located within a shared drive." [doc: G-SDDIFFS]
- And for any drive: "A parent doesn't appear in the parents list if the requesting user isn't a
  member of the shared drive and doesn't have access to the parent." [doc: G-SDDIFFS]
- QUESTION below: can a My Drive file returned for `'<folder>' in parents` carry a second parent,
  so that the same file is reached through two folders of the tree?

### E.3 Shortcuts [doc: G-SHORTCUTS]

- MIME type `application/vnd.google-apps.shortcut`; `shortcutDetails.targetId` points at the
  target; "A shortcut can only have one parent."
- "When the target is deleted, or when the current user loses access to the target, the user's
  shortcut pointing to the target breaks." What `files.get` on a broken shortcut's target returns
  is the ordinary `notFound` (K) by inference; a stated answer is **[not stated]**.
- "The title of a shortcut can differ from the target." "If the target's name is changed,
  previously created shortcuts retain the old title."
- A shortcut in the named folder can point outside it (to a file or folder anywhere). Shortcuts
  have no `size` and no checksums [doc: FILES]. Whether to follow them is a design choice.

### E.4 Folders with limited access [doc: G-LIMITED]

- A folder with `inheritedPermissionsDisabled=true` admits only owners/organizers and people
  added directly. Someone with inherited access "can see the restricted folder in Drive but can't
  open it."
- "If you try to list the children of such a folder, the result is always empty." Check
  `capabilities.canListChildren`. So a subfolder can silently list as empty for the connector's
  identity.
- The page says the value is `false` "if the requester's access to the folder's contents was
  removed by setting `inheritedPermissionsDisabled` to `false`", which contradicts its own
  definition (setting it to `true` limits access). Likely a typo; noted, not a design input.

## F. Reading content

### F.1 `files.export` (Google Docs, Sheets, Slides) [doc: F-EXPORT]

- `GET https://www.googleapis.com/drive/v3/files/{fileId}/export?mimeType=...`; `mimeType`
  "Required."; body empty; "If successful, this method returns the file content as bytes."
- "Note that the exported content is limited to 10 MB." G-DOWNLOADS: "Exported content is limited
  to 10 MB."
- No `supportsAllDrives` parameter on `files.export` [doc: F-EXPORT; DISCOVERY lists only `fileId`
  and `mimeType`]. Whether exporting a shared-drive file needs anything extra is **[not stated]**.
- "Note: Partial downloads are not supported while exporting Google Workspace documents." [doc:
  G-DOWNLOADS]
- cURL [example: G-DOWNLOADS]:
  `curl -L "https://www.googleapis.com/drive/v3/files/FILE_ID/export?mimeType=application/pdf"`.
  The `-L` hints at a redirect; whether `files.export` redirects is **[not stated]**.
- The response's `Content-Type` and charset are **[not stated]**.
- **Over 10 MB:** the error code, `reason` and body are **[not stated]**. G-ERRORS has no entry
  for it; `exportSizeLimitExceeded` is on no fetched page. QUESTION below.

### F.2 Export formats [doc: G-EXPORT]

| Source | Export formats (MIME type) |
|---|---|
| Documents | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `application/vnd.oasis.opendocument.text`, `application/rtf`, `application/pdf`, **`text/plain`**, `text/html`, `application/zip` (HTML zipped), `application/epub+zip`, **`text/markdown`** |
| Spreadsheets | xlsx, `application/vnd.oasis.opendocument.spreadsheet`, `application/pdf`, `application/zip`, `text/csv` and `text/tab-separated-values`, both "(first-sheet only)" |
| Presentations | pptx, `application/vnd.oasis.opendocument.presentation`, `application/pdf`, **`text/plain`** |
| Drawings | pdf, jpeg, png, svg |
| Apps Script | `application/vnd.google-apps.script+json` |
| Google Vids | `video/mp4`, but "Google Vids files cannot be exported using the `files.export` method or the `exportLinks` field" (gives `fileNotExportable`) |

- So: a Doc can be exported as Markdown (`text/markdown`, .md) and as plain text
  (`text/plain`, .txt). A Sheet has no text export beyond the first sheet's CSV/TSV; all sheets
  only as xlsx/ods/pdf/zip. Slides export as `text/plain`.
- "To view a list of all system supported export formats for a user, use the `get` method on the
  `about` resource with the `fields` parameter set to `exportFormats`." [doc: G-EXPORT].
  `exportFormats`: "A map of source MIME type to possible targets for all supported exports."
  [doc: ABOUT]; DISCOVERY: an object whose values are arrays of strings. A sample response is
  **[not stated]**.
- What Markdown dialect the export produces, and what happens to comments, suggestions, images
  and tables, is **[not stated]**.

### F.3 `files.get?alt=media` (blobs: plain text, Markdown, PDF) [doc: F-GET, G-DOWNLOADS]

- "If you provide the URL parameter `alt=media`, then the response includes the file contents in
  the response body. Downloading content with `alt=media` only works if the file is stored in
  Drive. To download Google Docs, Sheets, and Slides use `files.export` instead." [doc: F-GET]
- cURL: `curl -L "https://www.googleapis.com/drive/v3/files/FILE_ID?alt=media"` [example:
  G-DOWNLOADS].
- `Range` header: "You can specify the portion of the file you want to download by using a byte
  range with the `Range` header. For example:" `Range: bytes=500-999` [doc: G-DOWNLOADS].
- No size limit is stated for `alt=media`. Upload limit: "The maximum file size that users can
  upload is 5 TB" [doc: G-LIMITS].
- PDFs come back as PDF bytes; Drive offers no text extraction for a stored PDF through this
  method **[not stated]** anywhere. (The pages describe importing a PDF into a Doc as an upload
  feature, which a read-only connector cannot use.)
- `acknowledgeAbuse`: "Whether the user is acknowledging the risk of downloading known malware or
  other abusive files. This is only applicable when the `alt` parameter is set to `media` and the
  user is the owner of the file or an organizer of the shared drive in which the file resides."
  [doc: F-GET]. "Files identified as abusive (such as harmful software) are only downloadable by
  the file owner." and "Your application should interactively warn the user before using this
  query parameter." [doc: G-DOWNLOADS]. The error for an abusive file without it is
  **[not stated]**.
- Calling `alt=media` on a Doc: the body for `files.get` is **[not stated]**; for
  `revisions.get` it is `fileNotDownloadable` (K).

### F.4 `exportLinks` and `webContentLink`

Links for a browser [doc: G-DOWNLOADS]; whether a server may fetch `exportLinks` with a bearer
token, and whether that escapes the 10 MB export limit, is **[not stated]**.

### F.5 `files.download` (long-running operation) [doc: F-DOWNLOAD, G-LRO, OPS]

- `POST https://www.googleapis.com/drive/v3/files/{fileId}/download`, body empty, optional
  `mimeType` ("This field can only be set when downloading Google Workspace documents.") and
  `revisionId` ("This field can only be set when downloading blob files, Google Docs, and Google
  Sheets. Returns `INVALID_ARGUMENT` if downloading a specific revision on the file is
  unsupported."). Scopes: `drive`, `drive.file`, `drive.readonly`. Returns an Operation.
- Response [example: G-LRO]:
  ```
  {
    "done": true,
    "metadata": {
      "@type": "type.googleapis.com/google.apps.drive.v3.DownloadFileMetadata",
      "resourceKey": "RESOURCE_KEY"
    },
    "name": "NAME",
    "response": {
      "@type": "type.googleapis.com/google.apps.drive.v3.DownloadFileResponse",
      "downloadUri": "DOWNLOAD_URI",
      "partialDownloadAllowed": false
    }
  }
  ```
- Poll with `GET https://www.googleapis.com/drive/v3/operations/{name}`: "The user making the
  `get` method request must match the user who initiated the adjoining `download` method request
  or else the request fails." "If the operation is completed and you attempt to poll the `name`,
  you might get a `403 Forbidden` error with a status of `PERMISSION_DENIED`." [doc: G-LRO]
- Default MIME types if none given: Docs as docx, Sheets as xlsx, Slides as pptx [doc: G-LRO].
- "Download links generated for Google Docs or Sheets initially return a redirect." [doc: G-LRO]
- **CONFLICT on lifetime.** F-DOWNLOAD: "Operations are valid for 24 hours from the time of
  creation." G-LRO: "An LRO remains available for a minimum of 12 hours but in some cases can
  persist longer."
- With `revisionId`, `files.download` is the only documented way to fetch an **earlier** Doc
  revision's content by API (G.4). Whether `files.download` is bound by the 10 MB export limit is
  **[not stated]**.
- Cost: 200 quota units, like `files.export` [doc: G-LIMITS].

### F.6 When the owner has disabled download, copy and print

- Fields: `capabilities.canDownload`; `copyRequiresWriterPermission` ("Whether the options to
  copy, print, or download this file should be disabled for readers and commenters.");
  `downloadRestrictions.itemDownloadRestriction` ("applied directly by the owner or organizer.
  This doesn't take into account shared drive settings or DLP rules.") and
  `downloadRestrictions.effectiveDownloadRestrictionWithContext` ("This considers all restriction
  settings and DLP rules.") [doc: FILES]. `restrictedForWriters` "If true, download is also
  restricted for readers." [doc: FILES].
- Shared drives: `restrictions.copyRequiresWriterPermission` "will override the similarly named
  field to `true` for any file inside this shared drive"; `restrictions.downloadRestriction`
  [doc: DRIVES].
- "Before you download or export file content, verify that users can download the file using
  the `capabilities.canDownload` field on the `files` resource." [doc: G-DOWNLOADS]
- Admin DLP IRM (paraphrased): on every platform, users cannot download, print or copy the
  Google Drive files of Google Docs, Sheets or Slides. [doc: ADMIN-IRM]
- **What `files.export` or `alt=media` returns when download is restricted for the caller is
  [not stated].** No G-ERRORS entry covers it; `cannotDownloadFile` is on no fetched page. The
  only download-restriction error documented is `downloadRestrictedForRevision` (K), for blob
  revisions. QUESTION below.
- Client-side encrypted files (`application/vnd.google-gsuite.encrypted`, or
  `clientEncryptionDetails.encryptionState` `encrypted`) cannot be read as text without the key
  service; G-MCP-ELIG: "Content encrypted with CSE cannot be parsed by AI agents and is
  ineligible." (said of the MCP server).

## G. Change tracking

### G.1 `changes.getStartPageToken` [doc: CH-START]

- `GET https://www.googleapis.com/drive/v3/changes/startPageToken`; parameters `driveId` ("The ID
  of the shared drive for which the starting pageToken for listing future changes from that
  shared drive will be returned."), `supportsAllDrives`.
- Response: `startPageToken` (string) and `kind` (`drive#startPageToken`); "The page token doesn't
  expire."

### G.2 `changes.list` [doc: CH-LIST; defaults from DISCOVERY]

`GET https://www.googleapis.com/drive/v3/changes`, `pageToken` required in practice ("This should
be set to the value of 'nextPageToken' from the previous response or to the response from the
getStartPageToken method.").

| Parameter | Meaning (quoted) | DISCOVERY default |
|---|---|---|
| `pageToken` | see above | |
| `pageSize` | "If unspecified, at most 100 changes will be returned. The maximum value is 1000; values above 1000 will be coerced to 1000." | 100 (1..1000) |
| `driveId` | "The shared drive from which changes will be returned. If specified the change IDs will be reflective of the shared drive; use the combined drive ID and change ID as an identifier." | |
| `includeItemsFromAllDrives` | "Whether both My Drive and shared drive items should be included in results." | false |
| `supportsAllDrives` | "Whether the requesting application supports both My Drives and shared drives." | false |
| `includeRemoved` | "Whether to include changes indicating that items have been removed from the list of changes, for example by deletion or loss of access." | **true** |
| `includeCorpusRemovals` | "Whether changes should include the file resource if the file is still accessible by the user at the time of the request, even when a file was removed from the list of changes and there will be no further change entries for this file." | false |
| `restrictToMyDrive` | "Whether to restrict the results to changes inside the My Drive hierarchy." | false |
| `spaces` | `drive`, `appDataFolder` | `drive` |
| `includeLabels`, `includePermissionsForView` | as in D.1 | |

- **No folder parameter.** None of the parameters limits changes to a folder [doc: CH-LIST;
  DISCOVERY]. The connector must test each change's `file.parents` itself, and a file moved into
  or out of the folder (or into a subfolder) needs its ancestry walked. Whether a change's
  `file.parents` is the new parent after a move is implied by "the current state" (below) but
  not spelled out **[not stated]**.
- Response:
  ```
  {
    "changes": [ { object (Change) } ],
    "kind": string,
    "nextPageToken": string,
    "newStartPageToken": string
  }
  ```
  `nextPageToken`: "This will be absent if the end of the changes list has been reached. The page
  token doesn't expire." `newStartPageToken`: "The starting page token for future changes. This
  will be present only if the end of the current changes list has been reached. The page token
  doesn't expire." [doc: CH-LIST]
- "Entries in the `changes` collection are in chronological order (the oldest changes appear
  first)." [doc: G-CHANGES]
- "If it's not listed, the client application should store the `newStartPageToken` in the
  response for future use." [doc: G-CHANGES]
- What `changes.list` answers for a malformed or unusable token is **[not stated]**. QUESTION
  below.
- Shared drive [example: G-ENABLESD]:
  `GET .../changes/startPageToken?supportsAllDrives=true&driveId=SHARED_DRIVE_ID`, then
  `GET .../changes?pageToken=START_PAGE_TOKEN&supportsAllDrives=true&includeItemsFromAllDrives=true&driveId=SHARED_DRIVE_ID`.

**Change** [doc: CH]:

| Field | Type | Description (quoted) |
|---|---|---|
| `kind` | string | `drive#change` |
| `changeType` | string | "Possible values are `file` and `drive`." |
| `removed` | boolean | "Whether the file or shared drive has been removed from this list of changes, for example by deletion or loss of access." |
| `file` | File | "Present if the type is file and the file has not been removed from this list of changes." |
| `fileId` | string | "The ID of the file which has changed." |
| `time` | string, date-time | "The time of this change (RFC 3339 date-time)." |
| `driveId` | string | "The ID of the shared drive associated with this change." |
| `drive` | Drive | "Present if the changeType is drive, the user is still a member of the shared drive, and the shared drive has not been deleted." |

- The `file` inside a change obeys `fields`: G-FIELDS shows `changes.list` with
  `fields=changes(file(permissions(role)))`. Without a `fields` value, which File fields a change
  carries is **[not stated]**; ask for e.g.
  `fields=nextPageToken,newStartPageToken,changes(changeType,removed,fileId,time,file(id,name,mimeType,parents,trashed,modifiedTime,version))`.

**What a change is, and what it is not** [doc: G-ABOUTCHANGES, G-CHANGEOV, G-CHANGELOGS]:

- "The collection works by providing the current state of each item, if and only if the item has
  changed since a given point in time."
- "A change does not provide a delta between revisions. Applications that need to know which
  properties have changed should persist the previously known state of the item and compare."
- "There can only be one entry per file or shared drive in the change log at a time. Each time
  that file or shared drive changes, a new ID is created for that entry, and it replaces the
  previous entry." [doc: G-CHANGEOV]
- A metadata-only change (rename, star, share) is also a change: "An alteration made to a file's
  content or metadata of a file or shared drive." [doc: G-CHANGEOV]. Some appear only in the
  acting user's log: "when a user opens the file for the first time, or marks the file as
  Starred, those changes only appear in that users' change log." [doc: G-CHANGEOV]
- **Tombstones. CONFLICT on the name.** G-ABOUTCHANGES: "Change entries for items no longer
  available to a user are marked as `deleted` in the change entry. Only the ID of the item is
  available in the change entry."; G-CHANGELOGS: "the change log entry will say `deleted`". The
  Change resource has no `deleted` field; it has `removed` [doc: CH; DISCOVERY]. Treat
  `removed: true` as the tombstone.
- "The change entry does not distinguish between loss of access and deletion. When a file moves
  between corpora it may also appear as a delete even though the user still retains access to the
  file." Use `includeCorpusRemovals` "to disambiguate corpus moves from loss of access." [doc:
  G-ABOUTCHANGES]
- **Shared drives:** "Each shared drive has its own change log." "If a file belongs to a shared
  drive ... replaying the user's change log alone won't correctly update the file's status."
  The shared drive's change log has to be replayed as well, for every change. "When a user
  becomes a member of a shared drive, a single change event for the shared drive appears in the
  user's change log. ... The user does not receive changes for items contained inside the shared
  drive when they become a member." [doc: G-ABOUTCHANGES]. "After a file is moved to a shared
  drive, that shared drive change log continues logging changes for that file, not the user change
  log." [doc: G-CHANGELOGS]
- "If a non-member is granted file access to individual items in a shared drive, changes to those
  items are tracked in the user change log, not the shared drive change log." [doc: G-CHANGELOGS]
- **Service account with a shared folder (C.2):** whether its user change log carries changes to
  files it reaches only through a folder shared with it (not "Shared with me" items it opened) is
  **[not stated]**. The user change log is described as "A record of all changes that a user made
  to every editable file in their My Drive" [doc: G-CHANGEOV], which does not obviously cover
  files others edit in a folder shared with the account. CONFLICT-shaped with G-CHANGES ("an
  efficient way to detect all file changes, including those shared with a user"). QUESTION below.
- ACL changes: "when a file's ACL is changed, it's only reflected in the change log of the
  following users: The file's owner, any service accounts on the ACL, and any users directly
  impacted by the ACL change." [doc: G-CHANGEOV]

**Push.** `changes.watch` can POST notifications to an HTTPS webhook; "Notifications don't contain
details about the changes." [doc: G-CHANGES]; a watch expires after at most "604800 seconds (1
week) for `changes`", default 3600 seconds, with "no automatic way to renew" [doc: G-PUSH]. Needs a
public HTTPS endpoint with a valid certificate [doc: G-PUSH]. G-CHANGES: "While not documented in
`changes.watch`, the method does require a `pageToken` parameter". Not needed for polling.

### G.3 Trash and deletion as seen by a reader

- `files.list` includes trashed files unless `trashed = false` [doc: F-LIST].
- Files in the trash "are automatically deleted after 30 days" [doc: G-DELETE]. "Only the file
  owner can trash a file, but other users can still access the file in the owner's trash until
  it's permanently deleted." [doc: G-DELETE]
- A file trashed with its parent folder has `trashed: true`, `explicitlyTrashed: false` [doc:
  FILES].
- A permanently deleted file: `files.get` gives `notFound` (K) by the stated meaning of 404 ("the
  user doesn't have read access to a file, or the file doesn't exist") [doc: G-ERRORS]. In the
  change log, `removed: true` (above).

### G.4 Revisions [doc: REV, REV-LIST, REV-GET, G-REVISIONS, G-CHANGEOV]

- `GET https://www.googleapis.com/drive/v3/files/{fileId}/revisions`; `pageSize` "If unspecified,
  at most 200 revisions will be returned. The maximum value is 1000"; `pageToken`. No
  `supportsAllDrives` parameter [doc: REV-LIST; DISCOVERY].
- Response: `revisions[]`, `nextPageToken` (string), `kind` (`drive#revisionList`);
  `nextPageToken` "typically valid for several hours" [doc: REV-LIST].
- Default fields: "the `revisions.list` method only returns the `id`, `mimeType`, `kind`, and
  `modifiedTime` fields" [doc: G-REVISIONS].
- **Revision** fields: `id`, `mimeType`, `kind` (`drive#revision`), `modifiedTime` ("The last time
  the revision was modified (RFC 3339 date-time)."), `lastModifyingUser`, `exportLinks`,
  `published`, `publishAuto`, `publishedOutsideDomain`, `publishedLink` (Docs Editors only);
  `keepForever`, `md5Checksum`, `size`, `originalFilename` (binary files only) [doc: REV].
- **What a revision of a Google Doc is:** "A version of the file representing a change to the
  file's contents (not metadata)." "A change to a Docs, Sheets, or Slides file gets a new revision.
  Each time the content changes, Drive creates a new revision history entry for that file.
  However, these editor file revisions may be merged together, so the API response might not show
  all changes to a file." [doc: G-CHANGEOV]
- "Important: The list of revisions returned by this method might be incomplete for files with a
  large revision history, including frequently edited Google Docs, Sheets, and Slides. Older
  revisions might be omitted from the response, meaning the first revision returned may not be the
  oldest existing revision." [doc: REV-LIST]. The order of the list is **[not stated]**.
- "Google Docs, Drawings, and Slides revisions auto-increment the revision numbers. However, the
  series of numbers might have gaps if revisions are deleted, so you shouldn't rely on sequential
  numbers to retrieve revisions." [doc: G-LRO]
- **Role needed:** writer or above (headline 3) [doc: G-REVISIONS, G-ROLES]; check
  `capabilities.canReadRevisions` [doc: G-LRO, FILES]. The error a reader gets from
  `revisions.list` is **[not stated]**.
- Content of an old Doc revision: `revisions.get?alt=media` gives `fileNotDownloadable` for
  Workspace documents [doc: G-ERRORS, G-LRO]; use the revision's `exportLinks` (browser) or
  `files.download` with `revisionId` (F.5).
- **CONFLICT on `headRevisionId`.** FILES and G-CHANGEOV: available only for binary files ("The
  `headRevisionId` is only available for blob files in Drive."). G-LRO, without a restriction:
  "You can use the value of the `headRevisionId` field from the `files` resource to download the
  latest revision."

### G.5 Which signal says the content changed

What the pages state, no more:

- `modifiedTime`: "The last time the file was modified by anyone" [doc: FILES]. Whether a
  metadata-only change (rename, move, share, description) moves `modifiedTime` is
  **[not stated]**.
- `version`: "reflects every change made to the file on the server, even those not visible to the
  user" [doc: FILES]. So it moves on more than content edits; how much more is **[not stated]**.
- Revisions are content-only ("not metadata") [doc: G-CHANGEOV] but need writer access and may be
  merged or incomplete (G.4).
- `md5Checksum` / `sha256Checksum` are content hashes for blobs only (E).
- The push header `X-Goog-Changed` distinguishes `content` from `properties`, `parents`,
  `children`, `permissions`, for `files.watch` notifications only [doc: G-PUSH].
- No page says how long after an edit `modifiedTime`, `version` or a change entry appear, or how
  long a Doc must be idle before its revision settles **[not stated]**.
- For reading a Doc once after it has stopped changing, the pages offer `modifiedTime` (compare with the
  clock) and nothing that marks a Doc as finished. A content restriction (`contentRestrictions[].readOnly`)
  signals "Setting an item to a finalized state, such as approved" but "isn't a way to create an
  immutable record" [doc: G-CONTENT]. The design must pick; QUESTION below.

## H. Shared drives

- Flags: "you must include the `supportsAllDrives=true` query parameter in your requests" for
  `files.get`, `files.list`, `changes.list`, `changes.getStartPageToken`, `permissions.list`,
  `permissions.get` (and the write methods) [doc: G-ENABLESD]. `files.list` also needs
  `includeItemsFromAllDrives=true` (and `corpora`/`driveId` as in D.3); `changes.list` needs
  `includeItemsFromAllDrives=true` and `driveId` for a shared drive's log.
- 404 hint: "If the file is located in a shared drive, and you're using the `files.get` method,
  make sure the `supportsAllDrives` query parameter is set to `true`." [doc: G-ERRORS, `notFound`]
- `drives.get`: `GET https://www.googleapis.com/drive/v3/drives/{driveId}`; Drive `id` "The ID of
  this shared drive which is also the ID of the top level folder of this shared drive."; `name`,
  `createdTime`, `capabilities.canDownload`, `restrictions.*` [doc: DRIVES-GET, DRIVES]. Needs
  `drive` or `drive.readonly` [doc: DRIVES-GET].
- Fields that differ in shared drives [doc: G-SDDIFFS, FILES]: `driveId`, `trashedTime`,
  `trashingUser` only there; `owners`, `ownedByMe`, `permissions`, `shared`, `writersCanShare`
  never there.
- Roles: shared-drive roles are `organizer` (Manager), `fileOrganizer` (Content Manager),
  `writer` (Contributor), `commenter`, `reader` (Viewer); "The `owner` role isn't allowed in
  shared drives." [doc: G-ABOUTSD, G-ROLES]. Permissions are "strictly expansive" inside a shared
  drive, except folders with limited access [doc: G-ABOUTSD, G-LIMITED].
- What a service account needs to read one: membership (any role grants "Read the content of the
  file" and "Read the list of items in the folder" [doc: G-ROLES]) or a file/folder permission;
  writer or above to read revisions (G.4). A non-member with only folder access is tracked in
  the user change log, not the shared drive's [doc: G-CHANGELOGS]. Whether a non-member can
  call `changes.getStartPageToken` with that `driveId` is **[not stated]** (`teamDriveMembershipRequired`
  exists for "a user attempts to access a shared drive in which they're not a member" [doc:
  G-ERRORS]).
- Item cap 500,000 including trash; depth 100 [doc: G-ABOUTSD, G-MANAGESD].

## I. Domain-wide delegation versus sharing the folder: summary

| | DWD, impersonating a person (C.1) | Folder shared with the service account (C.2) | One person's refresh token (C.3) |
|---|---|---|---|
| Who acts | Workspace super admin: client ID + `drive.readonly` in Manage Domain Wide Delegation | Folder owner/editor (or shared-drive manager): share with the SA's address | The person: consent in the browser |
| What it can read | Everything the impersonated person can read (only the named folder by the connector's own choice) | Only what is shared with the SA | Everything the person can read |
| Scope class | `drive.readonly`: Restricted | same | same |
| Change log | the person's user log, plus each shared drive's log | the SA's user log **(coverage of shared-folder files [not stated])**, or the shared drive's log if a member | the person's |
| Domain-wide shares | seen, as the person | not seen (headline 8) | seen |
| Quota | "API calls by a service account are considered to be using a single account." [doc: G-LIMITS]; per impersonated user or per SA **[not stated]** | the SA is the user | the person |

## J. Quotas and limits [doc: G-LIMITS unless marked]

- "As of May 1, 2026, the usage limits for this API were updated. Google Cloud projects that made
  any use of this API between November 2025 and April 2026 will continue with their previously
  set usage quotas. Cloud projects created on or after May 1, 2026 are subject to the new API
  quotas."

| Limit | Value |
|---|---|
| Per minute per project | 1,000,000 quota units |
| Per minute per user per project | 325,000 quota units |
| Per day per project | 1 TB (described as "the maximum number of bytes your Google Cloud project can egress within a 24-hour period before charges apply") |
| Daily billing threshold, per day per project | 400,000,000 quota units |

- **CONFLICT on the daily limit.** The quota table's "Per day per project" is egress bytes, "1
  TB"; the "Daily billing threshold" section says "This per day per project limit defines the
  maximum number of quota units your Google Cloud project can use within a 24-hour period before
  charges apply." with "400,000,000 quota units". The page also says "Provided you stay within the
  per-minute quotas, there's no limit to the number of requests you can make per day."
- Costs per call: `files.get` (without `alt=media`), `revisions.get` (without), `about.get`,
  `drives.get`: 5 units; `files.list`, `changes.list`, `revisions.list`, `drives.list`: 100;
  `files.export`, `files.download`, `files.get` with `alt=media`, `revisions.get` with
  `alt=media`: 200; `changes.getStartPageToken`, `operations.get`: 5.
- Charges: "Full billing details will be shared later in 2026 with at least 90 days' notice
  before any changes take effect." and "Exceeding the quota request limits is planned to incur
  charges to your Google Cloud billing account later in 2026." Whether charging has started by
  2026-11-13 is **[not stated]**. TOOLS-SAFETY: "Later in 2026, following 90 days of notice" quota
  increases "will require Google Cloud billing to be enabled" and "API usage over standard daily
  thresholds will generate charges on your Google Cloud bill."
- "API calls by a service account are considered to be using a single account."
- Backoff: wait `min(((2^n)+random_number_milliseconds), maximum_backoff)`;
  `random_number_milliseconds` "less than or equal to 1,000"; `maximum_backoff` "typically 32 or
  64 seconds"; "At some point, clients should be prevented from retrying indefinitely."
- `Retry-After`: on no page fetched **[not stated]**.
- Batch: "You're limited to 100 calls in a single batch request." "Google Drive doesn't support
  batch operations for media, either for upload or download, or for exporting files." "A set of n
  requests batched together counts toward your usage limit as n requests" [doc: G-PERF].
- Sizes: export 10 MB (F.1); folder 500,000 items; depth 100; shared drive 500,000 items (H).
- MCP quota numbers on the same page are for the separate Drive MCP server, not the REST API, and
  look inverted (per project 325,000, per user per project 1,000,000). Noted only.

## K. Errors

G-ERRORS (title "Resolve errors") is the only Drive errors page. Every sample body there uses the
older shape: an `error` object holding `errors[]` (each with `domain`, `reason`, `message`), `code` and `message`;
none shows `error.status` or `error.details[]` (compare google-auth.md C.2). Several samples are
not valid JSON as printed (trailing commas after the last member; a stray `s` after the 429 sample's
`message` value); below they are given with those removed.

### K.1 Status summary [doc: G-ERRORS]

| Status | Meaning (quoted) |
|---|---|
| 400 | "The request cannot be fulfilled due to a client error in the request." |
| 401 | "The request contains invalid credentials." |
| 403 | "The request was received and understood, but the user doesn't have permission to perform the request." Also: "These errors mean that a usage limit has been exceeded or the user doesn't have the correct privileges. To determine the cause, evaluate the `reason` field of the returned JSON." |
| 404 | "The requested page couldn't be found." / "the requested resource isn't accessible or doesn't exist." |
| 429 | "Too many requests to the API." |
| 500, 502, 503, 504 | "Unexpected error arises while processing the request." Fix: "use exponential backoff to retry the request." |
| 410 Gone | No Drive page lists it **[not stated]**. A deleted or no-longer-visible file is a 404 `notFound` (K.2); an unusable change token's status is also **[not stated]** (G.2). |

### K.2 The bodies the connector must tell apart [doc: G-ERRORS; bodies are examples there]

401 `authError` ("the access token that you're using is either expired or invalid. This error
can also be caused by missing authorization for the requested scopes."):
```
{"error": {"errors": [{"domain": "global", "reason": "authError", "message": "Invalid Credentials",
  "locationType": "header", "location": "Authorization"}], "code": 401, "message": "Invalid Credentials"}}
```

403 `userRateLimitExceeded` ("the per-user limit has been reached. This might be a limit from the
Google Cloud console or a limit from the Drive backend."):
```
{"error": {"errors": [{"domain": "usageLimits", "reason": "userRateLimitExceeded",
  "message": "User Rate Limit Exceeded"}], "code": 403, "message": "User Rate Limit Exceeded"}}
```

403 `rateLimitExceeded` ("the project's rate limit has been reached. This limit varies depending
on the type of request."):
```
{"error": {"errors": [{"domain": "usageLimits", "message": "Rate Limit Exceeded",
  "reason": "rateLimitExceeded"}], "code": 403, "message": "Rate Limit Exceeded"}}
```

429 `rateLimitExceeded` ("the user has sent too many requests in a given amount of time"):
```
{"error": {"errors": [{"domain": "usageLimits", "reason": "rateLimitExceeded",
  "message": "Rate Limit Exceeded"}], "code": 429, "message": "Rate Limit Exceeded"}}
```

403 `dailyLimitExceeded` ("the API limit for your project was reached"; "This error appears when
the application's owner has set a quota limit to cap usage of a particular resource."):
```
{"error": {"errors": [{"domain": "usageLimits", "reason": "dailyLimitExceeded",
  "message": "Daily Limit Exceeded"}], "code": 403, "message": "Daily Limit Exceeded"}}
```

403 `sharingRateLimitExceeded` ("the user reaches a sharing limit and is often linked with an
email limit"; a write-side error, the connector never shares):
```
{"error": {"errors": [{"domain": "global", "message": "Rate limit exceeded. User message: \"These item(s) could not be shared because a rate limit was exceeded: filename",
  "reason": "sharingRateLimitExceeded"}], "code": 403, "message": "Rate Limit Exceeded"}}
```

404 `notFound` ("the user doesn't have read access to a file, or the file doesn't exist"):
```
{"error": {"errors": [{"domain": "global", "reason": "notFound", "message": "File not found {fileId}"}],
  "code": 404, "message": "File not found: {fileId}"}}
```
(The inner and outer messages differ by a colon, as printed.)

403 `fileNotDownloadable` (`revisions.get` with `alt=media` on a Workspace document):
```
{"error": {"errors": [{"domain": "global", "reason": "fileNotDownloadable",
  "message": "Only files with binary content can be downloaded. Use Export with Docs Editors files."}],
  "code": 403, "message": "Only files with binary content can be downloaded. Use Export with Docs Editors files."}}
```
**CONFLICT:** listed under the heading "401 errors", with `"code": 403` in its body.

403 `downloadRestrictedForRevision` (heading) / `download_restricted_for_revision` (`reason` in the
body), "when the user cannot download a blob file revision":
```
{"error": {"errors": [{"domain": "global", "reason": "download_restricted_for_revision",
  "message": "This revision cannot be downloaded by the authenticated user."}], "code": 403,
  "message": "This revision cannot be downloaded by the authenticated user."}}
```

403 `fileNotExportable` (Google Vids):
```
{"error": {"errors": [{"domain": "global", "reason": "fileNotExportable",
  "message": "Google Vids does not support files.export. Use files.download with Vids files."}],
  "code": 403, "message": "Google Vids does not support files.export. Use files.download with Vids files."}}
```

403 `domainPolicy` ("the policy for the user's domain doesn't allow access to Drive by your app"):
```
{"error": {"errors": [{"domain": "global", "reason": "domainPolicy",
  "message": "The domain administrators have disabled Drive apps."}], "code": 403,
  "message": "The domain administrators have disabled Drive apps."}}
```

403 `teamDriveMembershipRequired` ("a user attempts to access a shared drive in which they're not
a member"):
```
{"error": {"errors": [{"domain": "global", "reason": "teamDriveMembershipRequired",
  "message": "The attempted action requires shared drive membership."}], "code": 403,
  "message": "The attempted action requires shared drive membership."}}
```

403 `appNotAuthorizedToFile` ("your app isn't on the ACL for the file"; the `drive.file` case):
```
{"error": {"errors": [{"domain": "global", "reason": "appNotAuthorizedToFile",
  "message": "The user has not granted the app {appId} {verb} access to the file {fileId}."}],
  "code": 403, "message": "The user has not granted the app {appId} {verb} access to the file {fileId}."}}
```

400 `badRequest` (the page's example):
```
{"error": {"code": 400, "errors": [{"domain": "global", "location": "orderBy", "locationType": "parameter",
  "message": "Sorting is not supported for queries with fullText terms. Results are always in descending relevance order.",
  "reason": "badRequest"}],
  "message": "Sorting is not supported for queries with fullText terms. Results are always in descending relevance order."}}
```

### K.3 The rest of G-ERRORS, in one line each (write-side; the connector should never see them)

`illegalKeepForeverModification` (400), `invalidSharingRequest` (400), `activeItemCreationLimitExceeded`,
`cannotModifyInheritedTeamDrivePermission`, `emailMessage`, `fileOwnerNotMemberOfTeamDrive`,
`fileWriterTeamDriveMoveInDisabled`, `insufficientFilePermissions`, `myDriveHierarchyDepthLimitExceeded`,
`numChildrenInNonRootLimitExceeded`, `sendNotificationEmails` (heading; `reason` in the body is
`sendNotificationEmail`), `storageQuotaExceeded`, `teamDriveFileLimitExceeded`,
`teamDriveHierarchyTooDeep`, `teamDrivesFolderMoveInNotSupported`, `teamDrivesParentLimit`,
`UrlLeaseLimitExceeded` (all 403 unless marked).

### K.4 Asked about, not documented

- `downloadQuotaExceeded`, `exportSizeLimitExceeded`, `cannotDownloadFile`: on no page fetched
  (searched in every saved page) **[not stated]**. The status, `domain`, `reason` and `message`
  for (a) an export over 10 MB, (b) a download refused by download restrictions or IRM, and (c) a
  per-file download quota are all **[not stated]**. QUESTION below.
- 403 versus 429: G-LIMITS says a quota overrun gives 403 and "Additional rate limit checks on the
  Drive backend might also generate a `429: Rate limit exceeded` response"; G-ERRORS documents
  `rateLimitExceeded` under both 403 ("project's rate limit") and 429 ("too many requests in a
  given amount of time"). All three rate-limit reasons share `domain: usageLimits`. Both statuses
  must be treated as retryable with backoff; `dailyLimitExceeded` is a cap the owner set ("remove
  any usage caps").
- **CONFLICT on `quotaUser`.** G-ERRORS (under `userRateLimitExceeded` and
  `sharingRateLimitExceeded`): "If one user is making numerous requests on behalf of many users
  of a Google Workspace account, consider a service account with domain-wide delegation using the
  `quotaUser` parameter." G-FIELDS: "Per-user quotas are always enforced by the Drive API, and the
  user's identity is determined from the access token passed in the request. The `quotaUser` and
  `userIp` parameters can only be used for anonymous requests against public files."
- Long-running operations use the canonical codes (G-LRO table): e.g. `PERMISSION_DENIED` 403,
  `NOT_FOUND` 404, `RESOURCE_EXHAUSTED` 429 ("Retry with exponential backoff. Quota might become
  available over time."), `UNAVAILABLE` 503, `INVALID_ARGUMENT` 400 [doc: G-LRO].
- Whether Drive errors also carry `error.status` and `error.details[]` (AIP-193 form) besides
  `errors[]` is **[not stated]** for Drive; none of its samples shows them.
- An expired `files.list` page token: "If the token is rejected for any reason, it should be
  discarded" [doc: F-LIST]; the status and `reason` are **[not stated]**.
- Token lacking the scope for a method (e.g. export with `drive.metadata.readonly`): 401
  `authError` "can also be caused by missing authorization for the requested scopes" [doc:
  G-ERRORS]; whether it is 401 or 403 in practice is **[not stated]** beyond that sentence.

## L. On the customer's side

- **Enable the API** in the Cloud project that owns the credentials (G-EVENTS and G-MCP show
  enabling "Google Drive API" for their flows; a Drive page saying it for plain REST use was not
  among those read **[not stated]** here).
- **Delegation** (C.1): super admin, client ID, `https://www.googleapis.com/auth/drive.readonly`;
  up to 24 hours.
- **Sharing** (C.2): someone with share rights shares the folder with the service account's
  address as Viewer (reader) for content, Editor (writer) if revisions are wanted (G.4).
  Shared-drive alternative: a manager adds the SA as a member.
- **Consent** (C.3): the person signs in once with `access_type=offline` (google-auth.md B.1).
  For an Internal app, "use of restricted or sensitive scopes doesn't require further review by
  Google" [doc: WS-CONSENT].
- **What an admin can switch off** [doc: ADMIN-APPS, ADMIN-IRM, ADMIN-SHARING]:
  - restrict the Drive service, or block the app, or restrict the high-risk Drive scopes
    (`drive.readonly` among them): an untrusted app's tokens are revoked;
  - unconfigured third-party apps: "Don't allow users to access any third-party apps" (the
    setting's name);
  - internal apps: "Trust internal apps";
  - DLP IRM: no download, print or copy; G-MCP-ELIG says for the MCP server that such items are
    ineligible for AI agents; for the REST API the effect is F.6's open point;
  - external sharing off: effect on a service account **[not stated]** (C.2);
  - an admin's change can take up to 24 hours [doc: ADMIN-APPS, ADMIN-SHARING].
- G-ERRORS `domainPolicy` ("The domain administrators have disabled Drive apps.") is the one
  documented error tied to an admin switch.

## M. Policy (quoted, not interpreted)

From WS-POLICY ("Google Workspace user data and developer policy"):

- Scope of the policy: it covers "Gmail, Google Chat, Google Drive, Google Sheets, and other Google
  Workspace APIs, MCPs, and other developer tools requesting access to user data."
- Internal use: "Note: Certain Google OAuth API Scopes (the "Restricted Scopes") are subject to
  additional requirements as described in this guideline. There are also exceptions to the
  verification process that we recommend you review. If an app is only used by users within your
  own organization, then these additional requirements do not apply."
- Approved Drive use cases: "Built-in and web apps that provide local sync or automatic backup of
  users' Drive files."; "Productivity and educational applications (for example, a task
  management, note taking, workgroup communications, and classroom collaboration applications)
  that only use Restricted Scopes to handle Drive files (or their metadata or permissions) via the
  application's user interface."; "Reporting and security applications that provide user or
  customer insight into how files are shared or accessed."
- Not allowed for Drive (among others): "Backup of user or app content from a developer's app or
  project to Drive."; "Using Drive as a replacement for a large-scale content delivery network
  (CDN)."
- Minimum permissions: "Don't attempt to "future proof" your access to user data by requesting
  access to information that might benefit services or features that have not yet been
  implemented."
- Agents: "For example, granularly require users to confirm the MCP, tool, skill, or other
  agentic behavior invocation."
- Limited use, scope: "These requirements apply to data derived from both Sensitive and Restricted
  scopes."
- "Limit your use of data to providing or improving your appropriate use case or features that are
  visible and prominent in the requesting application's user interface."
- "Transfers of data are not allowed, except:" first exception: "To provide or improve your
  appropriate use case or user-facing features that are visible and prominent in the requesting
  application's user interface and only with the user's consent;" (others: security purposes,
  applicable laws, a merger with the user's prior consent).
- "Do not allow humans to read user data, unless:" first exception: "You have obtained and
  documented the user's explicit consent or affirmative agreement to view specific messages, files,
  or other data".
- **AI / ML:** prohibited: "Transferring, selling, or using user data to create, train, or improve
  a machine learning or artificial intelligence model beyond that specific user's personalized
  model for the appropriate use case or user-facing feature."
- "Note: Remember, our Terms of Service prohibits the scraping, building databases (including
  databases for model training purposes), or otherwise creating permanent copies of Google User
  data. This includes keeping cached copies longer than permitted by the cache header."
- Security for Restricted scopes: "Applications accessing Restricted Scopes must demonstrate that
  they adhere to certain security practices." Required measures include encryption "At rest on
  your systems", "Keeping user data and credentials, specifically tokens such as OAuth access and
  refresh tokens, encrypted at rest.", "Ensuring keys and key material are managed appropriately,
  such as stored in a hardware security module or equivalent-strength key management system.",
  and "Protecting against prompt injection techniques by either using Google Cloud Platform's
  Model Armor or other prompt injection protection." Also "Required security measures for
  Restricted Scopes also include following the Cloud Application Security Assessment (CASA)."
- Disclosure wording: "The use of information received from Google Workspace scopes will adhere
  to the Google User Data Policy, including the Limited Use requirements."

From UD-POLICY ("Google API Services User Data Policy", last updated February 15, 2024):

- "Note: If your app is only used by users within your own domain, then these requirements do not
  apply." (said of "Additional Requirements for Specific API Scopes")
- Limited Use "requirements apply to the raw data obtained from the scopes and data aggregated,
  anonymized, or derived from them."
- Its list of prohibited transfers names advertising platforms, data brokers, ads and
  credit-worthiness; it does not name AI or ML models. The AI/ML clause is in WS-POLICY.

From DRIVE-TERMS: "If you're using restricted scopes, the restricted scope verification process
and a security assessment are required."

**When verification and the security assessment do not apply** (quoted):

- G-SCOPES: "If you store restricted scope data on servers (or transmit), then you must go through
  a security assessment." (no exception stated on that page)
- RESTRICTED-VERIF: "Every app that requests access to Google users' restricted data and has the
  ability to access data from or through a third-party server must go through a security
  assessment from Google-empanelled security assessors."; reassessment "at least every 12 months".
- RESTRICTED-VERIF, "Exceptions to verification requirements": "If your app is going to be used in
  any of the scenarios described in the following sections, you don't need to submit it for
  review." The scenarios:
  - "Internal use only": "This means the app is used only by people in your Google Workspace or
    Cloud Identity organization. The project must be owned by the organization, and its OAuth
    consent screen needs to be configured for an Internal user type."
  - "Service-owned data only": "If your app uses a service account to access only its own data,
    and it doesn't access any user data (linked to a Google Account), then you don't need to submit
    for verification."
  - "Domain-wide installation": "If you plan for your app to only target users of a Google
    Workspace or Cloud Identity organization and always use domain-wide installation, then your app
    won't require brand verification. However, if your app utilizes restricted or sensitive scopes,
    app verification is required." (This is the Marketplace domain install, a different thing from
    a service account's domain-wide delegation.)
  - "Personal use" and "Projects used in Development, Testing, or Staging tiers".
- VERIF-EXC (paraphrased): "Internal Use apps" are apps that only people in the project's Google
  Workspace or Cloud Identity organization use, in a project the organization owns, whose OAuth
  Consent Screen is set for internal use; and every app that works with Google APIs has to follow
  Google's API Services User Data Policy whether it has been verified or not.
- WS-CONSENT: "For apps used only internally by your Google Workspace organization, scopes aren't
  listed on the consent screen and use of restricted or sensitive scopes doesn't require further
  review by Google."
- A service account with domain-wide delegation reading users' files is not named in any of these
  exceptions **[not stated]**; "Service-owned data only" excludes "user data". QUESTION below.

Agents and AI on the Drive side:

- G-MCP (Drive MCP server only): user OAuth clients for agents "must have Use this client for an
  AI-powered agent selected"; service accounts with delegation "can't select" it and are "governed
  by the domain-wide delegation settings". WS-CREDS: "If your app is an AI agent that takes actions
  on behalf of users, select Use this client for an AI-powered agent." and "You can't change this
  setting after you create the client." Whether this applies to a REST (non-MCP) reader whose
  output feeds an AI memory is **[not stated]**. QUESTION below.
- `capabilities.canAccessViaGenAi` exists on File [doc: FILES]; G-MCP-ELIG defines eligibility for
  the MCP server (IRM, Context-Aware Access via `canDownload`, CSE, spam/malware). Whether the REST
  API enforces anything based on it is **[not stated]**.
- TOOLS-SAFETY: "Google may require verification, such as in the form of an audit, of adherence to
  our user data policies."

---

## Open questions for the connector

1. QUESTION: Which signal means the content of this Doc changed: `modifiedTime`, `version`, or a
   new revision? `version` counts "every change ... even those not visible to the user";
   whether a rename, move or share moves `modifiedTime` is not stated; revisions are content-only
   but need writer access and "may be merged" (G.4, G.5). This decides both reading once when idle
   and re-reading on change.
   **[cairn]** Neither is relied on for change: a document is read once, when its `modifiedTime`
   is `settle_hours` (24) old, and later edits are not followed. A document is chosen by being
   put into the folder; taken out, left out until a pull has run, and put back, it is read
   again. Whether a move changes
   `modifiedTime` does not matter: the first pull after a switch-on takes stock of what the
   folder lists, and a document not there then is read however old it is.
2. QUESTION: Does a service account that was only *shared* a folder (C.2) see changes to that
   folder's files in its own `changes.list`? The user change log is defined as changes "a user
   made", while G-CHANGES says the collection detects changes "including those shared with a
   user" (G.2). If not, C.2 must poll the tree with `files.list` and `modifiedTime`.
   **[cairn]** `changes.list` is not used. Every pull asks `files.get` for the folder itself
   (`id,mimeType,trashed,capabilities/canListChildren`, so a folder the account cannot use is an
   error naming `folder_id`, not an empty listing), then lists its direct children with exactly
   `q='<folder_id>' in parents and trashed = false`, every page.
3. QUESTION: What does `files.export` return for a Doc over 10 MB (status, `reason`, body), and do
   `files.download` (LRO) or `exportLinks` lift the limit (F.1, F.4, F.5)?
   **[cairn]** Neither is used. A 403 on content whose reason is not a rate limit, the daily
   limit, `domainPolicy`, `insufficientPermissions`, `ACCESS_TOKEN_SCOPE_INSUFFICIENT` or
   `authError` skips that one document once, the reason never repeated. Content is read to at
   most 10 MiB (cairn's "10 MB"); past it, and for a file that is not a Doc whose `size` is
   over it (without a request), the document is skipped once as too large. A Doc's `size` is
   not used.
4. QUESTION: What do `files.export` and `files.get?alt=media` return when download is restricted
   for the caller (owner restriction, shared-drive restriction, DLP IRM)? `cannotDownloadFile` is
   undocumented (F.6, K.4). Can the connector rely on `capabilities.canDownload` alone?
   **[cairn]** Not alone: `canDownload` false skips the document without a request, and a 403
   on its content skips it too (question 3).
5. QUESTION: Is `drive.readonly` (Restricted) really needed, or does `drive.file` cover a folder
   shared with a service account in the Drive UI (B)? With `drive.file` the documented failure is
   `appNotAuthorizedToFile` (K.2).
   **[cairn]** `drive.readonly`, the narrowest scope the pages establish, and the only one asked
   for.
6. QUESTION: Does the "Internal use only" exemption cover a self-hosted server whose Cloud project
   belongs to the customer's own organization, for each of C.1 (DWD), C.2 (shared folder) and C.3
   (one person's token)? The exemption text speaks of an Internal OAuth consent screen; DWD and
   shared-folder service accounts use no consent screen (M).
7. QUESTION: To list a folder that lives in a shared drive, is `corpora=drive&driveId=...` required,
   or do `supportsAllDrives=true&includeItemsFromAllDrives=true` with the default `user` corpus
   find its children (D.3)? The guide's recursive sample uses only the two flags.
   **[cairn]** The two flags on every listing, `supportsAllDrives=true` on the folder and on a
   file's `alt=media`; no `corpora` or `driveId`. `files.export` is sent with `mimeType` only.
8. QUESTION: Can a My Drive file still have two parents (E.2: FILES "can only have one parent
   folder" vs G-ABOUTFILES "can be in multiple folders"), so that a tree walk meets the same file
   twice, and should shortcuts inside the folder be followed (E.3)?
   **[cairn]** No tree walk, and no shortcut followed: only the folder's direct children of
   three types (a Doc, `text/markdown`, `text/plain`) are read; every other type is passed over.
9. QUESTION: Does an admin's setting that turns external sharing off stop a user from sharing a folder with a
   service account, which "do not belong to your Google Workspace domain" (C.2)?
10. QUESTION: Is quota counted per impersonated user or once for the service account under DWD
    ("API calls by a service account are considered to be using a single account"), and does
    `quotaUser` work for authenticated calls (K.4 CONFLICT)?
11. QUESTION: Without `orderBy` the order is arbitrary; is `orderBy=modifiedTime desc` stable
    enough to stop a poll early at the first file older than the last run (D.1)? Ties and
    concurrent edits are not covered.
    **[cairn]** No `orderBy`: every page is read (at most 1,000 pages of 1,000), and the due
    documents are sorted by `modifiedTime`, then id. At most 25 contents a pull, within a budget
    of 1,200 requests.
12. QUESTION: What does `changes.list` answer for an invalid start token, and what does
    `files.list` answer for a rejected `pageToken` (G.2, D.4)? Change tokens "doesn't expire";
    list tokens are "typically valid for several hours".
13. QUESTION: Which default fields does `files.list` return, four or five (`resourceKey`) (D.4)?
    Harmless if the connector always sets `fields`, which it must anyway.
14. QUESTION: Does Drive's error body carry `error.details[]`/`error.status` in addition to the
    `error.errors[]` shown on G-ERRORS (K.4)? The fake must pick one shape.
    **[cairn]** The reason is read from either (cairn.sources.google_api), and
    `userRateLimitExceeded` is a rate limit beside `rateLimitExceeded`.
15. QUESTION: Does "Use this client for an AI-powered agent" (or any agent rule) apply to a REST
    reader that feeds an AI memory service, and what does the WS-POLICY AI/ML clause allow for a
    memory compiled from the documents (M)?
16. QUESTION: What Markdown does `text/markdown` export produce (headings, tables, images,
    comments, suggestions), and is it stable enough to diff between reads (F.2)?
    **[cairn]** It is not diffed. The text is written as exported, decoded as UTF-8 with
    replacement, one leading byte-order mark dropped, and every `data:` image left out: a
    reference definition to a `data:` address is dropped and an image using one is replaced by
    its alt text.
17. QUESTION: Has charging for usage over the daily threshold started, and is the daily limit 1
    TB of egress or 400,000,000 quota units (J CONFLICT)?
