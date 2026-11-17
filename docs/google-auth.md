# Google OAuth 2.0 for a server: service account with domain-wide delegation, or one user's refresh token

Researched 2026-11-13. No Google API was called and no token was requested. Every page below
was fetched that day with a plain HTTPS GET (`?hl=en`), converted to text and read; final
URLs are given after redirects, with each page's "Last updated" line where it has one.

- **SA** = https://developers.google.com/identity/protocols/oauth2/service-account
  (last updated 2026-03-23). Read: everything except the Java and Python client-library
  walk-throughs (skimmed) and "Implement Cross-Account Protection" (not read).
- **WEB** = https://developers.google.com/identity/protocols/oauth2/web-server
  (last updated 2026-11-11). Read: the HTTP/REST parts of steps 1 to 5, "Errors" (both),
  "Call Google APIs" (HTTP/REST), "Refresh an access token", "Token revocation",
  "Time-based access". The PHP, Python, Ruby and Node.js samples and the "Complete example"
  were skimmed, not read line by line.
- **NATIVE** = https://developers.google.com/identity/protocols/oauth2/native-app
  (last updated 2026-10-30). Read: step 3 "Errors", step 4, step 5, "Refresh an access token".
- **OAUTH2** = https://developers.google.com/identity/protocols/oauth2 (last updated
  2026-10-29). Read from the top through "Refresh token expiration" and its session-control
  subsection; "Client libraries" and after not read.
- **DWD** = https://knowledge.workspace.google.com/admin/apps/control-api-access-with-domain-wide-delegation
  (asked for as https://support.google.com/a/answer/162106; last updated 2026-11-10). Read in full.
- **KEYS** = https://docs.cloud.google.com/iam/docs/keys-create-delete (asked for as
  cloud.google.com/iam/...; last updated 2026-11-13). Read: "Allow service account key
  creation", "Create a service account key" (Console and gcloud).
- **SA-CREDS** = https://docs.cloud.google.com/iam/docs/service-account-creds (last updated
  2026-11-13). Read for token lifetime and key expiry only.
- **MEET-AUTH** = https://developers.google.com/workspace/meet/api/guides/authenticate-authorize
  (last updated 2026-09-14). Read in full.

Sources outside the pages named in the task, tagged **[source: X]** where used:

- **AIP-193** = https://google.aip.dev/193 ("Errors"; https://cloud.google.com/apis/design/errors
  redirects here; page dated 2019-07-26, changelog to 2024-10-18).
- **AIP-194** = https://google.aip.dev/194 ("Automatic retry configuration").
- **AIP-4112** = https://google.aip.dev/auth/4112 (service account key file; page dated 2020-08-13).
- **ERROR-REASON** = https://raw.githubusercontent.com/googleapis/googleapis/master/google/api/error_reason.proto
  (Google's published `google.api.ErrorReason` enum, read as fetched that day).
- **DRIVE-ERRORS** = https://developers.google.com/workspace/drive/api/guides/handle-errors
  (last updated 2026-09-03). **This is the Drive API's page, not Meet's**; it is used only to
  show the older `error.errors[]` body shape that some Workspace APIs return.

Fetched, nothing used: https://docs.cloud.google.com/docs/quotas/troubleshoot (Cloud quota
errors; it covers Compute Engine and gRPC, not Meet).

Tags: **[doc: X]** = stated on page X; **[example: X]** = visible only in an example request or
response on page X, not stated as a rule; **[not stated]** = nowhere I could find in the pages
above. A **QUESTION:** line is an open point that matters for the design; a **CONFLICT** line
quotes two Google statements that disagree.

---

## Headline points (read first)

1. **The token endpoint's error bodies are never shown in full, and their HTTP statuses are
   never given.** SA's error table names two columns, "`error` field" and "`error_description`
   field" [doc: SA, "JWT error codes"]; no page shows a complete error body or the status that
   comes with `invalid_grant`, `unauthorized_client` or `access_denied` **[not stated]**. The only
   status given for a token-endpoint failure is "`400 Bad Request`" for DPoP failures
   [doc: WEB, NATIVE].
2. **The JWT `exp` limit is stated two ways on the same page.** "This value has a maximum of 1
   hour after the issued time" vs the error table: "It could also happen if the `exp` value is
   more than 65 mins in the future from the `iat` value" [doc: SA]. CONFLICT; QUESTION below.
3. **Service-account delegation errors map to the Admin console, not to the code.**
   `unauthorized_client` = "the service account is not authorized in the Admin console of the
   user's domain"; `access_denied` = "one or more requested scopes aren't authorized in the Admin
   console"; both say "it might take up to 24 hours for authorization to propagate"
   [doc: SA]. A user who does not exist is `invalid_grant` with `Not a valid email` or
   `Invalid email or User ID.` [doc: SA].
4. **A refresh token can stop working with no warning.** Unused six months, revoked, the
   100-per-client-per-account limit ("invalidates the oldest refresh token without warning"),
   and 7 days for an External app in "Testing" [doc: OAUTH2]. The error is `invalid_grant`
   [doc: WEB, NATIVE], body **[not stated]**.
5. **Meet accepts only user authentication; domain-wide delegation counts as user
   authentication.** "For Meet REST API, you can only authenticate using user authentication."
   and "domain-wide delegation impersonates a user and is therefore considered user
   authentication" [doc: MEET-AUTH].

---

## A. Service account with domain-wide delegation

### A.1 The key file

The JSON key file downloaded at key creation has this shape [example: KEYS, Console and gcloud
sections; AIP-4112]:

```
{
  "type": "service_account",
  "project_id": "PROJECT_ID",
  "private_key_id": "KEY_ID",
  "private_key": "-----BEGIN PRIVATE KEY-----\nPRIVATE_KEY\n-----END PRIVATE KEY-----\n",
  "client_email": "SERVICE_ACCOUNT_EMAIL",
  "client_id": "CLIENT_ID",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/SERVICE_ACCOUNT_EMAIL"
}
```

- `private_key_id` is the JWT header's `kid`; `client_email` is `iss` (and `sub` when the
  service account acts as itself). [doc: SA, "Addendum"; AIP-4112]
- The private key is PEM with literal `\n` in the JSON string. [example: KEYS]
- **CONFLICT on `token_uri`.** KEYS shows two examples of "the following format": the Console
  section's has `"token_uri": "https://accounts.google.com/o/oauth2/token"`, the gcloud
  section's has `"token_uri": "https://oauth2.googleapis.com/token"` [example: KEYS]. SA says
  the token request goes to `https://oauth2.googleapis.com/token` [doc: SA]. QUESTION below.
- Whether a key file can carry fields beyond these ten is **[not stated]**.
- "you cannot download it again" once downloaded; Google "does not keep a copy of your private
  keys". [doc: KEYS, SA]
- A user-managed key "never expires" by default, unless the project, folder or organization
  sets an expiry time. [doc: SA-CREDS]
- Key creation may be blocked: the `iam.disableServiceAccountKeyCreation` constraint; "If your
  organization was created on or after May 3, 2024, this constraint is enforced by default."
  [doc: KEYS]

### A.2 Granting delegation (Workspace super admin)

In the Admin console, Main menu > Security > Access and data control > API Controls, then
"Manage Domain Wide Delegation", "Add new", enter the
service account's **Client ID** (numeric) and "the list of scopes" in "OAuth scopes
(comma-delimited)", then "Authorize". [doc: SA; DWD gives the same path and adds, paraphrased,
a check under "View details" that each scope is on the list]

- Super admin only. [doc: SA, DWD]
- Propagation: "usually takes a few minutes ... might take up to 24 hours" [doc: SA];
  a change can take up to 24 hours, and most take less (paraphrased) [doc: DWD].
- Using the client email instead of the numeric ID, or a Google Group, gives
  `unauthorized_client` (A.6). [doc: SA]
- With multi-party approval on, a second super admin must approve. [doc: DWD]
- The service account's access is limited by "the permissions of the impersonated user and
  the OAuth scopes you authorize in the Admin console". [doc: SA]
- Service accounts "are not members of your Google Workspace account and aren't subject to
  domain policies set by Google Workspace administrators". [doc: SA, OAUTH2]

### A.3 The JWT

Form: `{Base64url encoded header}.{Base64url encoded claim set}.{Base64url encoded signature}`;
header and claim set are JSON "serialized to UTF-8 bytes, then encoded using the Base64url
encoding". [doc: SA]

Header [doc: SA]:

| Field | Rule |
|---|---|
| `alg` | mandatory, "has only one value: `"alg": "RS256"`" |
| `typ` | mandatory, "has only one value: `"typ": "JWT"`" |
| `kid` | optional, the key ID; "If an an incorrect Key ID is specified, all keys associated with the service account are tried ... Google reserves the right to reject tokens with incorrect key IDs." |

[example: SA] `{"alg":"RS256","typ":"JWT", "kid":"370ab79b4513eb9bad7c9bd16a95cb76b5b2a56a"}`

Claim set; "The required claims ... may appear in any order" [doc: SA]:

| Claim | Rule |
|---|---|
| `iss` | "The email address of the service account." |
| `scope` | "A space-delimited list of the permissions that the application requests." The error table adds "separated by spaces, not commas". |
| `aud` | "When making an access token request this value is always `https://oauth2.googleapis.com/token`." |
| `exp` | seconds since the epoch; "a maximum of 1 hour after the issued time" (see CONFLICT, headline 2) |
| `iat` | seconds since the epoch, "The time the assertion was issued" |
| `sub` | additional claim: "The email address of the user for which the application is requesting delegated access." Without permission to impersonate, the response "will be an error". |

[example: SA]
```
{
  "iss": "761326798069-r5mljlln1rd4lrbhg75efgigp36m78j5@developer.gserviceaccount.com",
  "sub": "some.user@example.com",
  "scope": "https://www.googleapis.com/auth/prediction",
  "aud": "https://oauth2.googleapis.com/token",
  "exp": 1328554385,
  "iat": 1328550785
}
```

- **Signature:** "The only signing algorithm supported ... is RSA using SHA-256"; "Sign the
  UTF-8 representation of the input using SHA256withRSA (also known as RSASSA-PKCS1-V1_5-SIGN
  with the SHA-256 hash function)"; input is `{header}.{claim set}` (both encoded); the signature
  is then Base64url encoded. [doc: SA]
- **CONFLICT on `aud` in the examples.** The rule says `aud` is "always
  `https://oauth2.googleapis.com/token`". Decoding the page's two signed example JWTs gives
  `"aud":"https://www.googleapis.com/oauth2/v4/token"` (the "signed and is ready for transmission"
  example) and `"aud":"https://accounts.google.com/o/oauth2/token"` (the raw POST and curl
  examples). [doc and example: SA; decoded on 2026-11-13] Whether those values are accepted is
  **[not stated]**. QUESTION below.
- **CONFLICT on padding.** The page says Base64url throughout, and its error table says the
  assertion "must be Base64-encoded, without newlines or padding equal signs"; its own
  Base64url header example ends in `=` (`...NTZhIn0=`). [doc and example: SA] QUESTION below.

### A.4 The token request

[doc: SA, "Request an access token"]

- `POST https://oauth2.googleapis.com/token`, HTTPS, body URL-encoded:
  `Content-Type: application/x-www-form-urlencoded` [example: SA raw dump].
- Parameters, both required:
  - `grant_type` = `urn:ietf:params:oauth:grant-type:jwt-bearer` ("URL-encoded as necessary");
  - `assertion` = "The JWT, including signature."

[example: SA]
```
POST /token HTTP/1.1
Host: oauth2.googleapis.com
Content-Type: application/x-www-form-urlencoded

grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3Mi...
```

### A.5 The response

[example: SA, "Response handling"; reproduced as on the page, which lacks a comma after `scope`]
```
{
  "access_token": "1/8xbJqaOZXSUZbHLl5EOtu1pxz3fmmetKx9W8CV4t79M",
  "scope": "https://www.googleapis.com/auth/prediction"
  "token_type": "Bearer",
  "expires_in": 3600
}
```

- Fields: `access_token`, `scope`, `token_type`, `expires_in`. [example: SA] A field list for
  this flow is **[not stated]**; only the example shows them.
- "Access tokens can be reused during the duration window specified by the `expires_in`
  value." [doc: SA]
- **Lifetime:** "By default, these tokens expire after 1 hour." [doc: SA-CREDS]. SA gives
  `expires_in: 3600` only in the example. [example: SA]
- **Refresh token: [not stated] whether one is ever returned.** SA says only: "When an access
  token expires, then the application should generate another JWT, sign it, and request
  another access token." [doc: SA] OAUTH2: the 100-refresh-token limit "does not apply to
  service accounts" [doc: OAUTH2].
- HTTP status of a success: **[not stated]** on SA.

### A.6 Errors from the token endpoint (JWT flow)

The table "JWT error codes" gives `error` and `error_description` values. [doc: SA] It does not
give HTTP statuses **[not stated]**, and shows no complete body **[not stated]**.

| `error` | `error_description` | Meaning (quoted or close paraphrase) |
|---|---|---|
| `unauthorized_client` | `Unauthorized client or scope in request.` | "the service account is not authorized in the Admin console of the user's domain" |
| `unauthorized_client` | `Client is unauthorized to retrieve access tokens using this method, or client not authorized for any of the scopes requested.` | authorized "using the client email address rather than the client ID (numeric)", or "a Google Group was used" |
| `access_denied` | (any value) | "one or more requested scopes aren't authorized in the Admin console" |
| `admin_policy_enforced` | (any value) | "unable to authorize one or more scopes requested due to the policies of their Google Workspace administrator" |
| `invalid_client` | (any value) | "The OAuth client or JWT token is invalid or incorrectly configured." |
| `deleted_client` | (any value) | the client was deleted; "Deleted clients can be restored within 30 days" |
| `invalid_grant` | `Not a valid email` or `Invalid email or User ID.` | "The user doesn't exist." |
| `invalid_grant` | `Invalid JWT: Token must be a short-lived token (60 minutes) and in a reasonable timeframe. Check your 'iat' and 'exp' values and use a clock with skew to account for clock differences between systems.` | "Usually, it means that the local system time is not correct." Also `exp` more than 65 minutes after `iat`, or `exp` lower than `iat`. |
| `invalid_grant` | `Invalid JWT Signature.` | wrong key, or the key was "deleted, disabled, or has expired", or bad encoding |
| `invalid_scope` | `Invalid OAuth scope or ID token audience provided.` | no scopes, or a scope that doesn't exist |
| `disabled_client` | `The OAuth client was disabled.` | "The key used to sign the JWT assertion is disabled." |
| `org_internal` | `This client is restricted to users within its organization.` | project limited to one Cloud organization |

- How much clock skew is tolerated is **[not stated]**; the page says to sync with Google NTP.
- **The task's wording vs the page.** The task names "a user not in the domain" as an
  `invalid_grant` cause and "`unauthorized_client` when delegation was not granted for a
  scope". The page says: `invalid_grant` = "The user doesn't exist" (not "not in the
  domain"); a scope not delegated is **`access_denied`**; `unauthorized_client` is for a client
  not authorized at all, or "not authorized for any of the scopes requested". What a user who
  exists but sits in another domain gets is **[not stated]**. QUESTION below.
- The Java sample says "The argument for the `createDelegated()` method must be a user which
  belongs to your Workspace account." [doc: SA]

---

## B. One user's refresh token

### B.1 How the refresh token is obtained (context only)

- Authorization endpoint `https://accounts.google.com/o/oauth2/v2/auth`; to get a refresh
  token set `access_type=offline`: "This value instructs the Google authorization server to
  return a refresh token and an access token the first time that your application exchanges
  an authorization code for tokens." [doc: WEB]
- The code exchange response includes `refresh_token` "only ... if you set the `access_type`
  parameter to `offline`" [doc: WEB]; for installed apps "refresh tokens are always returned"
  [doc: NATIVE].
- `prompt` (`none`, `consent`, `select_account`): "If you don't specify this parameter, the
  user will be prompted only the first time your project requests access." [doc: WEB] That a
  second consent without `prompt=consent` returns no new refresh token is **[not stated]** in
  those words; WEB says so only for DPoP: "you must use the `prompt=consent` parameter ... to
  ensure a new refresh token is issued" [doc: WEB, step 5 note].

### B.2 The refresh request

[doc: WEB and NATIVE, "Refresh an access token"]

- `POST https://oauth2.googleapis.com/token`, HTTPS, parameters "in the request body",
  `Content-Type: application/x-www-form-urlencoded` [example: WEB, NATIVE].

| Name | Value |
|---|---|
| `client_id` | "The client ID obtained from the API Console." |
| `client_secret` | **"Optional"**; NATIVE adds "not applicable to requests from clients registered as Android, iOS, or Chrome applications" |
| `grant_type` | "must be set to `refresh_token`" |
| `refresh_token` | "The refresh token returned from the authorization code exchange." |
| `DPoP` (header) | optional; "If provided, the returned tokens are bound to this key." |

[example: WEB, NATIVE]
```
POST /token HTTP/1.1
Host: oauth2.googleapis.com
Content-Type: application/x-www-form-urlencoded
DPoP: DPOP_PROOF_JWT

client_id=your_client_id&
refresh_token=refresh_token&
grant_type=refresh_token
```

- **CONFLICT on the client secret for a web client.** WEB marks `client_secret` "Optional",
  and its example sends none. OAUTH2 says "a JavaScript application does not require a secret,
  but a web server application does." [doc: WEB, OAUTH2] QUESTION below.
- **DPoP-bound refresh tokens:** "DPoP proofs must be included for token exchange requests made
  with DPoP-bound refresh tokens." [doc: WEB] A refresh token obtained without DPoP is not
  bound [inferred from "If provided, the returned tokens are bound"].

### B.3 The refresh response

"A successful exchange is indicated by a `200 OK` response containing a new access token."
[doc: WEB, NATIVE]

[example: WEB, NATIVE (the DPoP case)]
```
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
DPoP-Nonce: AN3XwJjZsjnb0ZuWkRlek8QU7wY-Zhf-5IP6tO0tORz0KgtDT1Bo8FX-w4nz3r5lnepI

{
  "access_token": "1/fFAGRNJru1FTz70BzhT3Zg",
  "expires_in": 3920,
  "scope": "https://www.googleapis.com/auth/drive.metadata.readonly https://www.googleapis.com/auth/calendar.readonly",
  "token_type": "Bearer"
}
```

- `expires_in`: "The remaining lifetime of the access token in seconds." `token_type`: "always
  `Bearer`, even when DPoP is used." `scope`: "space-delimited, case-sensitive strings".
  [doc: WEB, code-exchange field table]
- "Your application should ignore any unrecognized fields included in the response." [doc: WEB]
- Whether a refresh response can carry a new `refresh_token` (rotation) is **[not stated]**.
  QUESTION below.
- `refresh_token_expires_in` exists only "when the user grants time-based access" (code
  exchange response). [doc: WEB, NATIVE]
- Access-token lifetime for user tokens: "Access tokens have limited lifetimes" [doc: OAUTH2];
  a number is **[not stated]** (3920 appears in examples only).
- Token size limits: access tokens 2048 bytes, refresh tokens 512 bytes, authorization codes
  256 bytes; "Google reserves the right to change token size within these limits". [doc: OAUTH2]

### B.4 When a refresh token stops working

[doc: OAUTH2, "Refresh token expiration"]: "A refresh token might stop working for one of
these reasons":

- "The user has revoked your app's access."
- "The refresh token has not been used for six months."
- "The user changed passwords and the refresh token contains Gmail scopes."
- "The user account has exceeded a maximum number of granted (live) refresh tokens."
- "The user granted time-based access to your app and the access expired."
- An admin set a requested service to Restricted ("the error is `admin_policy_enforced`").
- For Google Cloud Platform APIs, an admin session length was exceeded.

And:

- **Testing mode:** "A Google Cloud Platform project with an OAuth consent screen configured for
  an external user type and a publishing status of "Testing" is issued a refresh token expiring
  in 7 days", unless the scopes are only name, email and profile. [doc: OAUTH2]
- **Count limit:** "a limit of 100 refresh tokens per Google Account per OAuth 2.0 client ID. If
  the limit is reached, creating a new refresh token automatically invalidates the oldest
  refresh token without warning." Plus "a larger limit on the total number of refresh tokens a
  user account or service account can have across all clients". [doc: OAUTH2]
- **Revocation by the app:** `https://oauth2.googleapis.com/revoke?token={token}`; 200 on
  success, "an HTTP status code `400` is returned along with an error code" otherwise;
  "Revocation removes all OAuth 2.0 scopes previously granted to a project, invalidating any
  issued access or refresh tokens for all clients registered under that project"; "it might
  take some time before the revocation has full effect". [doc: WEB]
- **Session control:** an expired Cloud session fails "with an error type `invalid_grant`; the
  `error_subtype` field can be used to distinguish ... (for example,
  `"error_subtype": "invalid_rapt"`)". [doc: OAUTH2] OAUTH2 also says "you must not use, or
  encourage the use of, user credentials for server-to-server deployment" in that context.
- **Deleted or disabled account:** "Otherwise, the user account may have been deleted or
  disabled." (under `invalid_grant`). [doc: WEB, NATIVE]

### B.5 The error when a refresh token no longer works

- Code: `invalid_grant`: "When refreshing an access token or using incremental authorization,
  the token may have expired or has been invalidated. Authenticate the user again and ask for
  user consent to obtain new tokens." [doc: WEB, NATIVE, "Errors" under step 3]
- HTTP status: **[not stated]** for `invalid_grant` on refresh. "`400 Bad Request`" is stated
  only for DPoP failures, which return `invalid_dpop_proof` or `use_dpop_nonce`; "Other
  failures might return `invalid_grant`." [doc: WEB, NATIVE]
- Body: field names `error` and `error_subtype` appear in OAUTH2's session-control paragraph;
  `error_description` appears as a column in SA's table. A full body is **[not stated]**.
  QUESTION below.
- Other codes the token endpoint can give for user flows: `invalid_client` ("The OAuth client
  secret is incorrect"), `deleted_client`, `admin_policy_enforced`, `org_internal`,
  `invalid_request`. [doc: WEB]

---

## C. Calling a Google API with the token, and its errors

### C.1 Sending the token

- "include the access token in a request to the API by including either an `access_token`
  query parameter or an `Authorization` HTTP header `Bearer` value. When possible, the HTTP
  header is preferable, because query strings tend to be visible in server logs." [doc: SA, WEB]
- [example: SA, WEB] `Authorization: Bearer access_token`
- OAUTH2: query-string tokens are possible "but we don't recommend it". [doc: OAUTH2]
- An access token is "valid only for the set of operations and resources described in the
  `scope` of the token request". [doc: OAUTH2]

### C.2 The JSON error envelope (general Google guidance, not Meet-specific)

No Meet page shows an error body (see google-meet-api.md, section H). The general shape is
from AIP-193 [source: AIP-193, "HTTP/1.1+JSON representation"]:

```
{
  "error": {
    "code": 429,
    "message": "...",
    "status": "RESOURCE_EXHAUSTED",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "RESOURCE_AVAILABILITY",
        "domain": "compute.googleapis.com",
        "metadata": { ... }
      },
      { "@type": "type.googleapis.com/google.rpc.LocalizedMessage", "locale": "en-US", "message": "..." },
      { "@type": "type.googleapis.com/google.rpc.Help", "links": [ ... ] }
    ]
  }
}
```

- `error.code` is "an HTTP status code, not the direct value of `google.rpc.Status.code`";
  `error.status` is "the enum version" (e.g. `NOT_FOUND`); `error.details[]` holds typed
  payloads; "All error responses must include an `ErrorInfo` within `details`". [source: AIP-193]
  This is a design rule for Google APIs; whether Meet follows it on every error is
  **[not stated]**.
- **Not found vs no permission:** "If the user does not have permission to access the resource
  or parent, regardless of whether or not it exists, the service must error with
  `PERMISSION_DENIED` (HTTP 403). Permission must be checked prior to checking if the resource
  or parent exists." Otherwise `NOT_FOUND` (HTTP 404). [source: AIP-193]
- **`ErrorInfo.reason` values** for the cases the task names [source: ERROR-REASON, examples
  with `"domain": "googleapis.com"`]:
  - `RATE_LIMIT_EXCEEDED`: rate quota hit (metadata `consumer`, `service`, `quota_metric`,
    `quota_limit`);
  - `ACCESS_TOKEN_SCOPE_INSUFFICIENT`: "the provided access token doesn't have at least one of
    the acceptable scopes required for the API";
  - `ACCESS_TOKEN_EXPIRED`: "the provided access token has expired";
  - `ACCOUNT_STATE_INVALID`: the account is "disabled or deleted".
  Which HTTP status each comes with is **[not stated]** in the proto.
- **The older `error.errors[]` shape** appears on Workspace pages for other APIs. Drive's 401
  [example: DRIVE-ERRORS; Drive API, not Meet]:
  ```
  {
    "error": {
      "errors": [
        {
          "domain": "global",
          "reason": "authError",
          "message": "Invalid Credentials",
          "locationType": "header",
          "location": "Authorization"
        }
      ],
      "code": 401,
      "message": "Invalid Credentials"
    }
  }
  ```
  Drive's rate limit: `"reason": "rateLimitExceeded"`, `"domain": "usageLimits"`, with `code`
  403 under "403 errors" and 429 under "429 errors" [example: DRIVE-ERRORS]. (The page's
  examples have trailing commas and a stray character, so they are not valid JSON as printed;
  shown above with those removed.) Whether Meet returns `errors[]`, `details[]` or both is
  **[not stated]**. QUESTION below.
- **What 401 means:** "the request doesn't contain a valid access token"; `authError` "occurs
  when the access token that you're using is either expired or invalid. This error can also be
  caused by missing authorization for the requested scopes." [doc: DRIVE-ERRORS, for Drive]
  For Meet, the status for a token lacking a Meet scope (401 or 403) is **[not stated]**.
- **Retrying:** AIP-194 lists `RESOURCE_EXHAUSTED` as "Generally non-retryable": "Retries
  therefore may not be expected to work for several hours"; `PERMISSION_DENIED`: "A client
  should not retry until it has permission"; `NOT_FOUND`: "should not retry until a resource is
  created". [source: AIP-194] The Meet limits page says to back off and retry a 429
  (google-meet-api.md, G). QUESTION below.

---

## Open questions for the connector

1. QUESTION: What HTTP status and full JSON body does `https://oauth2.googleapis.com/token`
   return for `invalid_grant`, `unauthorized_client` and `access_denied`? Only the `error` and
   `error_description` values are documented (A.6, B.5); a fake token server built from these
   notes must guess the status and envelope.
   **[cairn]** Only the `error` code is read, whatever the status, and it chooses the sentence;
   without a known code, a 5xx is Google's fault for the next run and anything else says to
   check the credential. `error_description` is never read.
2. QUESTION: What does the JWT flow return for a `sub` user who exists but is outside the
   delegating domain? The page names only "The user doesn't exist" (`invalid_grant`) (A.6).
   **[cairn]** A service account's `invalid_grant` says to check the subject, the clock and
   the key, so it covers that case if it is `invalid_grant`.
3. QUESTION: Is the `exp` limit 60 or 65 minutes after `iat`? "a maximum of 1 hour" vs "more
   than 65 mins" on the same page (A.3, headline 2).
   **[cairn]** 3,600 seconds, within both.
4. QUESTION: Do Meet API errors carry `error.details[]` with `ErrorInfo.reason` (AIP-193 style),
   the older `error.errors[].reason` (Drive style), or both, and which status does a token
   without a Meet scope get, 401 or 403 (C.2)?
   **[cairn]** Both shapes are read for a rate-limit reason; 401 and 403 that are not rate
   limits are treated alike, as access refused, and name the scope to check.
5. QUESTION: Is a 429 (`RESOURCE_EXHAUSTED`) worth retrying within one poll? AIP-194 calls it
   "Generally non-retryable" for quota exhaustion; the Meet limits page says to use exponential
   backoff (C.2).
   **[cairn]** Yes, four times, waiting 2^n seconds and up to one more (at most 32), never
   past the pull's deadline; then the pull fails and the next run goes on.
6. QUESTION: Is `client_secret` required on a refresh for a Web application client? WEB says
   "Optional"; OAUTH2 says a web server application requires a secret (B.2).
   **[cairn]** An `authorized_user` credential must hold one, and it is always sent.
7. QUESTION: Which `token_uri` is current in a key file, `https://oauth2.googleapis.com/token` or
   `https://accounts.google.com/o/oauth2/token`? KEYS shows both (A.1).
   **[cairn]** Both are accepted, and the request goes to `https://oauth2.googleapis.com/token`
   for either; a missing `token_uri` goes there too, and any other address is refused.
8. QUESTION: Does the server accept the older `aud` values seen in SA's signed examples
   (`https://www.googleapis.com/oauth2/v4/token`, `https://accounts.google.com/o/oauth2/token`), or
   only the stated `https://oauth2.googleapis.com/token` (A.3)?
   **[cairn]** `aud` is always `https://oauth2.googleapis.com/token`.
9. QUESTION: Must the Base64url segments be unpadded? The error table says "without ... padding
   equal signs"; the header example ends in `=` (A.3).
   **[cairn]** Unpadded.
10. QUESTION: Can a refresh response carry a new `refresh_token` that replaces the old one (B.3)?
    **[cairn]** Not kept if one comes: the credential is what the variable holds.
