# Google OAuth 2.0 for an installed ("Desktop app") client: obtaining one person's refresh token

Researched 2026-11-14. No Google endpoint was called with a client, no client was created and
nothing was signed in to. Every page below was fetched that day with a plain HTTPS GET
(`?hl=en` on Google's pages, User-Agent `curl/8`), saved under `oauth/raw/`, converted with
`tools/h2t.py` into `oauth/txt/` and read. Final URLs are given after redirects, with each page's
"Last updated" line; the Cloud Help Center pages show no date. The token side (how the refresh
token is used, when it stops working, the error envelope) is in google-auth.md (the repo's
`docs/google-auth.md`), section B; these notes cover only how the refresh token is **obtained**,
which google-auth.md B.1 marks "context only".

Google developer pages (base https://developers.google.com/identity):

- **NATIVE** = .../protocols/oauth2/native-app ("OAuth 2.0 for ... desktop applications";
  last updated 2026-10-30). Read in full. Its HTML was also grepped for `access_type`, `prompt`
  and `include_granted_scopes` to confirm they are absent from its parameter table (only
  `prompt=consent` occurs, once, in the DPoP note).
- **OAUTH2** = .../protocols/oauth2 (2026-10-29). Read in full.
- **WEB** = .../protocols/oauth2/web-server (2026-11-11). Read: "Create authorization
  credentials", the HTTP/REST parts of steps 1 to 5, "Errors", "Redirect URI validation rules",
  "Incremental authorization", the start of "Refresh an access token (offline access)". Language
  samples and "Complete example" not read. **This page is for web-server clients**; it is used
  only where NATIVE is silent, and every such use says so.
- **OIDC** = .../openid-connect/openid-connect (asked for as
  .../protocols/oauth2/openid-connect, linked from NATIVE's `state` row; 2026-10-29). Read:
  server flow steps 2 to 4, "Refresh tokens", "Prompting re-consent", "Authentication URI
  parameters". **Written for sign-in with a web client.**
- **POLICIES** = .../protocols/oauth2/policies ("Last modified: August 5, 2026"). Read in full.
- **LOOPBACK** = .../protocols/oauth2/resources/loopback-migration (2026-11-10). Read in full.
- **OOB** = .../protocols/oauth2/resources/oob-migration (2026-11-10). Read in full.
- **BEST** = .../protocols/oauth2/resources/best-practices (2026-10-30). Read in full.
- **GRANULAR** = .../protocols/oauth2/resources/granular-permissions (2026-11-10). Read the text
  through the first code examples under "Determine if your applications are affected"; the rest
  was grepped only (for "desktop", "installed", "checkbox", "refresh token").
- **DEVICES** = .../protocols/oauth2/limited-input-device (2026-10-16; NATIVE points to it for
  apps without a system browser). Read the introduction and "Allowed scopes".
- **SCOPES** = .../protocols/oauth2/scopes (2026-09-14). Read the introduction only; it does not
  say how scopes are passed.

Google Cloud Help Center (base https://support.google.com/cloud/answer/; no dates shown):

- **H-BRANDING** = 15549049 "Manage OAuth App Branding" (asked for as 10311615#user-type, linked
  from NATIVE; redirected). Read in full.
- **H-AUDIENCE** = 15549945 "Manage App Audience". Read in full.
- **H-CLIENTS** = 15549257 "Manage OAuth Clients" (linked from NATIVE). Read in full.
- **H-DATA** = 15549135 "Manage App Data Access". Read in full.
- **H-UNVERIFIED** = 7454865 "Unverified apps" (linked from NATIVE). Read in full.
- **H-VERIFY** = 13463073 "OAuth App Verification Help Center" (asked for as 9110914, linked
  from NATIVE; redirected). Read in full.
- **H-NOTNEEDED** = 13464323 "When is verification not needed". Read in full.
- **H-RESTRICTED** = 13464325 "Restricted Scopes". Read the Gmail and Drive lists.
- **H-PLATFORM** = 15548748 "Google Auth Platform Overview". Read in full.
- **H-START** = 15544987 "Get started with the Google Auth Platform". Read in full.
- **H-REQS** = 13464321 "Verification requirements". Read in full.
- **H-CASA** = 13465431 "Security Assessment". Read in full.
- **H-FAQ** = 13463817 "Frequently Asked Questions". Grepped only, for "unverified", "100",
  "Testing", "personal", "7 days", "refresh token".
- Fetched, title seen, not read: 13461325 "Submitting your app for verification", 13463816
  "Annual Recertification", 13464018 "Changes to approved app", 7650096 "Domain Limit Exceeded
  FAQ".

Google Workspace developer pages (base https://developers.google.com/workspace):

- **WS-CONSENT** = .../guides/configure-oauth-consent (2026-09-03). Read in full.
- **WS-CREDS** = .../guides/create-credentials (2026-10-12). Read "OAuth client ID credentials"
  (Web application, Desktop app).
- **WS-QUICK** = .../gmail/api/quickstart/python (2026-10-12). Read in full (one example of the
  end-to-end steps a person does).
- Fetched, nothing used: .../guides/enable-apis (2026-11-10).

Discovery document:

- **[discovery]** = https://accounts.google.com/.well-known/openid-configuration (Google's OpenID
  Connect discovery document; JSON, no revision field). Used to check both endpoints, the PKCE
  methods, the token endpoint's client authentication methods, the response modes and the `iss`
  flag.

Sources outside Google's developer pages, tagged **[source: X]**:

- **RFC6749** = https://www.rfc-editor.org/rfc/rfc6749.txt (OAuth 2.0; NATIVE links to its
  sections 4.1.3, 6 and 10.12). Read 4.1.2 to 4.1.4 and 5.2.
- **RFC7636** = https://www.rfc-editor.org/rfc/rfc7636.txt (PKCE; linked from NATIVE). Read 4.1 to
  4.6 and Appendix B.
- **RFC8252** = https://www.rfc-editor.org/rfc/rfc8252.txt (OAuth for native apps; linked from
  NATIVE "Further Reading"). Read 7.3 and 8.1 to 8.6.
- **BLOG** = https://developers.googleblog.com/making-google-oauth-interactions-safer-by-using-more-secure-oauth-flows/
  (asked for as developers.googleblog.com/2022/02/making-oauth-flows-safer.html, linked from
  NATIVE; dated Feb. 16, 2022). Read in full.
- **CLIENT-SECRETS** = https://raw.githubusercontent.com/googleapis/google-api-python-client/main/docs/client-secrets.md
  (Google's Python client library documentation of the `client_secrets.json` format; no date).
  Read in full. The only page found that shows the downloaded file's JSON.
- **AIP-4113** = https://google.aip.dev/auth/4113 ("gcloud CLI Integration", created and updated
  2020-07-31). Read in full; it shows an `authorized_user` file.

Tags: **[doc: X]** stated on page X; **[example: X]** visible only in an example on page X;
**[discovery]** from the discovery document; **[source: X]** from a non-Google-developer-page
source above; **[not stated]** nowhere in the pages above; **[inferred]** my reading, not a
statement. **QUESTION:** an open point that matters for the helper's design; **CONFLICT** two
provider statements that disagree, both quoted.

---

## Headline points (read first)

1. **NATIVE's parameter table has no `access_type` and no `prompt`.** It lists `client_id`,
   `redirect_uri`, `response_type`, `scope`, `code_challenge`, `code_challenge_method`, `state`,
   `login_hint` only [doc: NATIVE; checked in the HTML]. It says instead "Note that refresh
   tokens are always returned for installed applications." [doc: NATIVE]. **CONFLICT** with
   OIDC: "you can only obtain a refresh token the first time that you perform the code exchange
   flow" [doc: OIDC], and with NATIVE's own refresh section, which needs "if you requested offline
   access to the scopes associated with the token" [doc: NATIVE]. What a second authorisation by
   the same person returns is **[not stated]** on NATIVE except for DPoP (D.4). QUESTION 1.
2. **Where `state` comes back is stated two ways.** NATIVE: "in the URL fragment identifier
   (`#`) of the `redirect_uri`"; WEB: "in the URL query component (`?`) of the `redirect_uri`";
   OIDC: "All responses are returned in the query string". [doc: NATIVE, WEB, OIDC] The discovery
   document's `response_modes_supported` is `["query", "fragment", "form_post"]` [discovery]; no
   page says which mode the code flow uses for a Desktop client **[not stated]**. A browser
   does not send a fragment to an HTTP listener [inferred, not stated], so a listener could only
   check `state` if it is in the query. CONFLICT; QUESTION 2.
3. **Most authorization errors never reach the listener.** NATIVE's step-3 errors are
   "user-facing error messages instead of the expected authentication and authorization flows"
   (`redirect_uri_mismatch`, `invalid_request`, `org_internal`, `admin_policy_enforced`,
   `deleted_client`, ...); only a refusal is stated to come back: "`error=access_denied`
   indicates that the user declined the request." [doc: NATIVE] So the helper's listener can wait
   for a request that never comes. QUESTION 3.
4. **The client secret can be downloaded only once, at creation.** The Google Cloud Console
   shows an OAuth 2.0 client's secret, and lets it be downloaded, only when the client is created;
   afterwards it shows only the secret's last four characters (paraphrased) [doc: H-CLIENTS]. For a
   Desktop client NATIVE marks `client_secret` "Optional" at the code exchange and its sample
   request sends none, yet the discovery document lists only `client_secret_post` and
   `client_secret_basic` as token endpoint authentication methods [doc: NATIVE; discovery].
   QUESTION 4.
5. **PKCE's method defaults to `plain`.** "The value of the `code_challenge_method` defaults to
   `plain` if not present in the request that includes a `code_challenge`." [doc: NATIVE] The
   helper must send `code_challenge_method=S256` explicitly. NATIVE marks PKCE "Recommended" in
   the table and says "Always use PKCE" in the prerequisites (C).
6. **The 7-day expiry depends on the consent screen, not on the helper.** A test user's
   authorization ends seven days after consent, and a refresh token the client received by asking
   for `offline` access ends with it (paraphrased). [doc: H-AUDIENCE] Internal is only for
   projects that belong to a Google Cloud Organization [doc: H-AUDIENCE], so a personal Google account's own project is External
   [inferred]. No page states a 7-day expiry for "In production" [not stated], but that status
   brings the unverified-app screen and a cap of 100 new users in total for sensitive and restricted
   scopes [doc: H-AUDIENCE, H-UNVERIFIED]. Section I. QUESTION 5.
7. **A Desktop client registers no redirect URI, and the port is chosen at run time.** The
   console asks for nothing more to create a Desktop app's OAuth 2.0 credentials (paraphrased)
   [doc: H-CLIENTS]; the listener goes on "a random available port"
   [doc: NATIVE]. Yet NATIVE also says the value "must exactly match one of the authorized
   redirect URIs" (E). Whether a path is accepted is **[not stated]** by Google. QUESTION 6.
8. **Google's own error bodies for the code exchange are not shown.** Apart from the DPoP
   failures (`400 Bad Request`, `invalid_dpop_proof`, `use_dpop_nonce`), NATIVE lists no
   code-exchange errors (its `invalid_grant` entry sits under the authorization step); WEB lists
   one, `invalid_grant` "The supplied authorization code is invalid or in the wrong
   format." [doc: WEB]. Status and JSON shape come only from RFC 6749 5.2 [source: RFC6749] (H.2).

---

## A. Before the helper runs: project, API, consent screen, client

What the operator (or the person, in their own project) does in the Cloud Console. The helper can
do none of it: "OAuth clients cannot be created or modified programmatically." [doc: BEST]

1. **Enable the API.** "Any application that calls Google APIs needs to enable those APIs in the
   API Console." Steps: open the API Library, select a project, select the API, click Enable; "If
   prompted, enable billing."; "If prompted, read and accept the API's Terms of Service."
   [doc: NATIVE, "Enable APIs for your project"] Which APIs cairn's scopes need: see the per-API
   notes (gmail-api.md, google-drive-api.md, google-calendar-api.md, google-meet-api.md).
   H-DATA: the scopes table lists the scopes of enabled APIs only (paraphrased). [doc: H-DATA]
2. **Configure the consent screen** ("Google Auth platform"): Branding (app name, user support
   email), Audience (user type), Contact Information, agree to the User Data Policy, Create; then
   "If you selected External for user type, add test users": Audience > Test users > Add users,
   "Enter your email address and any other authorized test users, then click Save."
   [doc: WS-CONSENT] "For security reasons, you can't remove the OAuth 2.0 consent screen after
   you've configured it." [doc: WS-CONSENT]
3. **Declare the scopes** under Data Access > Add or Remove Scopes (for an app used outside one
   Workspace organization) [doc: WS-CONSENT]. A scope that is not registered brings the person the
   unverified app warning screen, even one verified before (paraphrased). [doc: H-DATA] When the
   scopes the app's code asks for differ from those on the OAuth consent screen's configuration
   page, people see the unverified app screen (paraphrased). [doc: H-UNVERIFIED]
4. **Create the client.** Clients > Create client > "Desktop app" > a name > Create; "Your client
   ID and client secret are generated and displayed in the Google Cloud console." [doc: NATIVE]
   WS-QUICK adds: "Save the downloaded JSON file as `credentials.json`" [doc: WS-QUICK]. Optional
   checkbox "Use this client for an AI-powered agent"; "You can't change this setting after you
   create the client." [doc: WS-CREDS]; H-CLIENTS (paraphrased): some of Google's MCP endpoints,
   Workspace tools among them, take only calls authorized with an agentic client ID. [doc:
   H-CLIENTS]
   Whether cairn's client should tick it is **[not stated]**; QUESTION 11.
5. **Unused clients are deleted.** A client used for no credential or token request and not
   modified for six months is deleted automatically, with an email 30 days before; afterwards
   calls to Google APIs with the deleted client's access or refresh tokens fail, and
   authorization requests give `deleted_client` (paraphrased). [doc: H-CLIENTS;
   POLICIES says the same] A cairn source that refreshes on schedule keeps the client in use
   [inferred].

## B. The downloaded client file

**What the file looks like.** No Google developer page in the set above shows the JSON a Desktop
client's download contains **[not stated]**. The only shape found is the Python client library's
documentation of `client_secrets.json` [example: CLIENT-SECRETS]:

```
{
  "installed": {
    "client_id": "837647042410-75ifg...usercontent.com",
    "client_secret":"asdlkfjaskd",
    "redirect_uris": ["http://localhost", "urn:ietf:wg:oauth:2.0:oob"],
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://accounts.google.com/o/oauth2/token"
  }
}
```

- The top-level key: "The format defines one of two client ID types": `web` ("Web
  application") and `installed` ("Installed application"). [doc: CLIENT-SECRETS]
- Members of `web` and `installed`: `client_id`, `client_secret`, `redirect_uris` ("A list of
  valid redirection endpoint URIs"), `auth_uri`, `token_uri`; "All of the above members are
  mandatory." Optional: `client_email`, `auth_provider_x509_cert_url`, `client_x509_cert_url`.
  [doc: CLIENT-SECRETS]
- **CONFLICT on the endpoints.** The example's `auth_uri` is `https://accounts.google.com/o/oauth2/auth`
  and its `token_uri` `https://accounts.google.com/o/oauth2/token` [example: CLIENT-SECRETS];
  NATIVE uses `https://accounts.google.com/o/oauth2/v2/auth` and `https://oauth2.googleapis.com/token`
  [doc: NATIVE], and so does the discovery document (`authorization_endpoint`,
  `token_endpoint`) [discovery]. The same split was found for service-account key files
  (google-auth.md A.1). QUESTION 7.
- **The example's `redirect_uris` includes the out-of-band URI** `urn:ietf:wg:oauth:2.0:oob`,
  which Google no longer supports (E.3) [example: CLIENT-SECRETS]. The page predates that change
  [inferred]; what a Desktop download holds in `redirect_uris` today is **[not stated]**.
- Whether today's download has further keys (for example `project_id`) is **[not stated]**.
- WEB calls the file `client_secret.json` and says "download the client_secret.json file from the
  API Console" (for a Web application client) [doc: WEB]; WS-QUICK names it `credentials.json`
  [doc: WS-QUICK]. The file name is not fixed **[inferred]**.

**Does a Desktop client have a secret, and is it secret?** CONFLICT among four pages:

- NATIVE: "Your client ID and client secret are generated and displayed in the Google Cloud
  console. Desktop apps are public clients and cannot keep secrets confidential." and "it is
  assumed that these apps cannot keep secrets (they are public clients)". [doc: NATIVE]
- OAUTH2, installed applications: "The process results in a client ID and, in some cases, a
  client secret, which you embed in the source code of your application. (In this context, the
  client secret is obviously not treated as a secret.)" [doc: OAUTH2]
- H-CLIENTS (paraphrased): public clients live on the person's device, cannot keep a secret
  safely there, and so use no client secret. [doc: H-CLIENTS]
- POLICIES: "Store your OAuth client information in a secure place and protect it, especially
  your client secret, just as you would a password." and "You must never commit client
  credentials into publicly available code repositories." [doc: POLICIES]
- RFC 8252 8.5: authorization servers that require a static secret for native apps "MUST treat
  the client as a public client" and "not accept the secret as proof of the client's identity".
  [source: RFC8252]
- The secret is shown once (paraphrased): it appears only when the client is created and can
  never be seen or fetched again, so it is to be kept somewhere safe such as Google Cloud Secret
  Manager. [doc: H-CLIENTS] A lost secret is replaced with rotation, which gives a new one; at
  most two secrets per client; OAuth flows refuse a disabled secret. [doc: H-CLIENTS] The
  once-only rule covers clients created after June 2025 and is to reach existing clients from
  November 2025 (paraphrased). [doc: H-CLIENTS]

## C. PKCE

[doc: NATIVE, "Step 1: Generate a code verifier and challenge"]

- Verifier: "a high-entropy cryptographic random string using the unreserved characters [A-Z] /
  [a-z] / [0-9] / "-" / "." / "_" / "~", with a minimum length of 43 characters and a maximum
  length of 128 characters." (RFC 7636 4.1 says nearly the same, as an assignment:
  `code_verifier = high-entropy cryptographic random STRING ...`.) A new one "for every
  authorization request".
- RFC 7636 recommends 32 random octets, base64url-encoded, giving "a 43-octet URL safe string"
  [source: RFC7636 4.1].
- Challenge, two methods: `S256` "(recommended)": "the Base64URL (with no padding) encoded SHA256
  hash of the code verifier", `code_challenge = BASE64URL-ENCODE(SHA256(ASCII(code_verifier)))`;
  `plain`: "the same value as the code verifier". [doc: NATIVE]
- Methods accepted: "The only supported values for this parameter are `S256` or `plain`."
  [doc: NATIVE]; the discovery document lists `code_challenge_methods_supported: ["plain",
  "S256"]` [discovery].
- Default: `plain` when `code_challenge_method` is absent (headline 5). RFC 7636: "If the client
  is capable of using "S256", it MUST use "S256"" [source: RFC7636 4.2].
- **What Google requires.** CONFLICT in tone, no statement that a request without PKCE is
  refused:
  - "Always use PKCE (Proof Key for Code Exchange) when requesting authorization codes."
    [doc: NATIVE, prerequisites]
  - Table: `code_challenge` and `code_challenge_method` are "Recommended". [doc: NATIVE]
  - "For desktop apps, using the Proof Key for Code Exchange (PKCE) protocol is strongly
    recommended" [doc: BEST]
  - The Console's Project Checkup checks that the installed app OAuth flow uses PKCE (paraphrased).
    [doc: H-PLATFORM]
  - Whether Google rejects a Desktop request without `code_challenge` is **[not stated]**.
- Test vector for a fake [source: RFC7636 Appendix B; recomputed 2026-11-14]: verifier
  `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` gives S256 challenge
  `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`.
- A wrong verifier at the exchange: RFC 7636 4.6 says the server returns `invalid_grant`
  [source: RFC7636]. NATIVE's `invalid_grant` entry: "If you are using a code verifier and
  challenge, the `code_challenge` parameter is invalid or missing." [doc: NATIVE]

## D. The authorization request

`GET https://accounts.google.com/o/oauth2/v2/auth` with query parameters; "The endpoint is only
accessible over SSL, and it refuses HTTP (non-SSL) connections." [doc: NATIVE] Same URL in the
discovery document's `authorization_endpoint` [discovery].

### D.1 Parameters NATIVE lists for installed apps

| Parameter | NATIVE says |
|---|---|
| `client_id` | Required. "You can find this value in the Cloud Console Clients page." |
| `redirect_uri` | Required. For loopback: `http://127.0.0.1:port` or `http://[::1]:port` (section E). |
| `response_type` | Required. "Set the parameter value to `code` for installed applications." |
| `scope` | Required. "A space-delimited list of scopes that identify the resources that your application could access on the user's behalf." |
| `code_challenge` | Recommended. "Specifies an encoded `code_verifier` that will be used as a server-side challenge during authorization code exchange." |
| `code_challenge_method` | Recommended. "This parameter must be used with the `code_challenge` parameter." `S256` or `plain`; default `plain`. |
| `state` | Recommended. Returned "as a `name=value` pair in the URL fragment identifier (`#`)" (CONFLICT, headline 2). "The OAuth client must prevent CSRF as called out in the OAuth2 Specification." |
| `login_hint` | Optional. "Set the parameter value to an email address or `sub` identifier, which is equivalent to the user's Google ID." The server uses it for "prefilling the email field in the sign-in form or by selecting the appropriate multi-login session". |

- **Several scopes:** space-delimited [doc: NATIVE]; "All scope values must be space-separated."
  [doc: OIDC]; the examples encode the space as `%20` (`scope=email%20profile`)
  [example: NATIVE].
- The sample URL [example: NATIVE, "Loopback IP address" tab]:
  ```
  https://accounts.google.com/o/oauth2/v2/auth?
   scope=email%20profile&
   response_type=code&
   state=security_token%3D138r5719ru3e1%26url%3Dhttps%3A%2F%2Foauth2.example.com%2Ftoken&
   redirect_uri=http%3A//127.0.0.1%3A9004&
   client_id=client_id
  ```
  It carries no `code_challenge`, no `code_challenge_method` and no `access_type`
  [example: NATIVE].

### D.2 Parameters NATIVE does not list (found on WEB and OIDC)

- **`access_type`** [doc: WEB, for web-server apps]: "Recommended"; values `online`, "which is
  the default value", and `offline`; "This value instructs the Google authorization server to
  return a refresh token and an access token the first time that your application exchanges an
  authorization code for tokens." OIDC: "the client does not receive a refresh token unless a
  value of `offline` is specified." [doc: OIDC] WEB also says "Server-side web applications,
  installed applications, and devices all obtain refresh tokens during the authorization
  process." [doc: WEB] H-AUDIENCE speaks of a client that asks for an `offline` access type
  [doc: H-AUDIENCE]. Whether an installed-app request needs it: see headline 1, QUESTION 1.
- **`prompt`** [doc: WEB]: "A space-delimited, case-sensitive list of prompts to present the
  user. If you don't specify this parameter, the user will be prompted only the first time your
  project requests access." Values `none` ("Must not be specified with other values."),
  `consent` ("Prompt the user for consent."), `select_account` ("Prompt the user to select an
  account."). OIDC: "When `prompt=consent` is included, the consent screen is displayed every
  time your app requests authorization of scopes of access, even if all scopes were previously
  granted to your Google APIs project. For this reason, include `prompt=consent` only when
  necessary." [doc: OIDC]
- **`include_granted_scopes`:** not for installed apps. NATIVE: "Note: Incremental authorization
  is not supported for installed apps or devices." and "incremental authorization with installed
  apps is not supported due to the fact that the client cannot keep the `client_secret`
  confidential." OIDC: "Note that you cannot do incremental authorization with the Installed App
  flow." [doc: NATIVE, OIDC]
- `enable_granular_consent`, `hd`, `hl`, `nonce`, `claims`, `display` exist on WEB or OIDC
  [doc: WEB, OIDC]; none is needed for this flow [inferred].

### D.3 What makes Google return a refresh token for an installed app

Quoted, all of it:

- "Note that refresh tokens are always returned for installed applications." [doc: NATIVE,
  `refresh_token` row of the code-exchange response]
- "You can refresh an access token without prompting the user for permission (including when the
  user is not present) if you requested offline access to the scopes associated with the token."
  [doc: NATIVE, "Refresh an access token"]
- "if your application loses the refresh token, the user will need to repeat the OAuth 2.0
  consent flow so that your application can obtain a new refresh token." [doc: NATIVE]
- WEB: "Note that the refresh token is only returned if your application set the `access_type`
  parameter to `offline` in the initial request" [doc: WEB, for web-server apps]
- OIDC: "you can only obtain a refresh token the first time that you perform the code exchange
  flow." [doc: OIDC]
- **CONFLICT**: "always returned" (NATIVE) vs "the first time" (WEB, OIDC) and "if you requested
  offline access" (NATIVE's own refresh section).

### D.4 A second authorisation by the same person

- NATIVE says nothing about it except for DPoP: "If your application already has a refresh token
  for the user and you want to obtain a new DPoP-bound refresh token, the user must revoke the
  existing grant or you must use the `prompt=consent` parameter in the initial authorization
  request to ensure a new refresh token is issued." [doc: NATIVE]
- Without `prompt`, "the user will be prompted only the first time your project requests access"
  [doc: WEB]. Whether the exchange after an unprompted second authorisation carries a
  `refresh_token` for a Desktop client is **[not stated]**. QUESTION 1.
- Each new refresh token counts toward "a limit of 100 refresh tokens per Google Account per OAuth
  2.0 client ID"; past it, the oldest is invalidated "without warning" [doc: OAUTH2;
  google-auth.md B.4]. Running the helper again does not by itself revoke the earlier token
  [inferred from the limit; not stated].

## E. The loopback redirect, and the other redirect methods

### E.1 Forms Google allows

- `http://127.0.0.1:port` or `http://[::1]:port`; "Query your platform for the relevant loopback IP
  address and start an HTTP listener on a random available port. Substitute port with the actual
  port number your app listens on." [doc: NATIVE]
- **`localhost`**: "It is also possible to use `localhost` in place of the loopback IP, but this
  configuration may cause issues with client firewalls. Most, but not all, firewalls allow
  loopback communication." [doc: NATIVE] OOB tells desktop clients to migrate "to using the
  loopback IP address (`localhost` or `127.0.0.1`) flow" [doc: OOB]; LOOPBACK counts
  `http://localhost:<port>` as a loopback value and its sample uses `redirect_uri=http://localhost:3000`
  [doc and example: LOOPBACK]. RFC 8252 8.3: "the use of localhost is NOT RECOMMENDED"
  [source: RFC8252]. Google allows it, does not recommend against it in words, warns of
  firewalls.
- **Path:** NATIVE's forms have none, and no Google page says whether `http://127.0.0.1:port/path`
  is accepted **[not stated]**. RFC 8252 7.3 allows a path (`http://127.0.0.1:{port}/{path}`)
  [source: RFC8252]. The custom-scheme row says "Note that the path should begin with a single
  slash" for that scheme only [doc: NATIVE]. Trailing slash: WEB says, for web clients, "the
  `http` or `https` scheme, case, and trailing slash ('`/`') must all match." [doc: WEB]; for a
  loopback URI **[not stated]**. QUESTION 6.
- **Port:** "a random available port" [doc: NATIVE]; whether it must be registered: the Desktop
  client has no redirect URI field (the console asks for nothing more to create a Desktop app's
  OAuth 2.0 credentials, paraphrased [doc: H-CLIENTS]); RFC 8252 7.3: "The
  authorization server MUST allow any port to be specified at the time of the request for
  loopback IP redirect URIs" [source: RFC8252]. **CONFLICT** with NATIVE's general rule: "The
  value must exactly match one of the authorized redirect URIs for the OAuth 2.0 client, which
  you configured in your client's Cloud Console Clients page." [doc: NATIVE]
- **IPv4 or IPv6:** RFC 8252: "It is RECOMMENDED that clients attempt to bind to the loopback
  interface using both IPv4 and IPv6 and use whichever is available." [source: RFC8252] Google
  says "Query your platform for the relevant loopback IP address" [doc: NATIVE].
- Status for Desktop apps: "this is the recommended mechanism for obtaining the authorization
  code" (if the platform supports a local web server); recommended usage "macOS, Linux, and
  Windows desktop apps"; "Set the application type to Desktop app." [doc: NATIVE] "You don't need
  to do anything related to this deprecation if you are using the loopback IP address flow on a
  Desktop app OAuth client as usage with that OAuth application type will continue to be
  supported." [doc: LOOPBACK]
- Redirect URI validation rules (for web clients, [doc: WEB, H-CLIENTS]): HTTPS, "Localhost URIs
  (including localhost IP address URIs) are exempt from this rule."; no raw IP hosts, "Localhost
  IP addresses are exempted from this rule."; no userinfo, no fragment, no `/..`, no wildcards.
  Whether they apply to a Desktop client's loopback URI is **[not stated]**.
- RFC 8252 8.3: "Clients should open the network port only when starting the authorization request
  and close it once the response is returned." and "Clients should listen on the loopback network
  interface only" [source: RFC8252].

### E.2 Loopback for other client types (deprecated, for context)

"The loopback IP address redirect option is DEPRECATED for Android, Chrome app and iOS OAuth client
types." [doc: NATIVE] Dates [doc: LOOPBACK]: March 14, 2022 new clients blocked; August 31, 2022
blocked for Android, Chrome app and iOS clients created before March 14, 2022; October 21, 2022
"all existing clients are blocked (including exempted clients)". LOOPBACK: "The deprecation is
applicable to the publishing statuses: Testing & In Production and user types: Internal &
External." BLOG (Feb. 16, 2022) gives only Mar 14, Aug 1 and Aug 31, 2022. [doc: BLOG] A Desktop
client is not affected (E.1).

### E.3 Manual copy/paste ("out of band", OOB)

- NATIVE: "The manual copy/paste option, also referred to as an out of band (OOB) redirect method,
  is no longer supported." [doc: NATIVE] The `redirect_uri_mismatch` entry: the parameter "may
  refer to the OAuth out-of-band (OOB) flow that has been deprecated and is no longer supported."
  [doc: NATIVE]
- Values: `urn:ietf:wg:oauth:2.0:oob`, `urn:ietf:wg:oauth:2.0:oob:auto`, `oob` [doc: OOB]; for all
  application types including "Desktop apps" [doc: OOB].
- Dates [doc: OOB]: February 28, 2022 new usage blocked; September 5, 2022 warning; October 3,
  2022 deprecated for clients created before February 28, 2022; January 31, 2023 "all existing
  clients are blocked (including exempted clients)". BLOG lists only the first three (Feb 28,
  Sep 5, Oct 3, 2022) [doc: BLOG].
- **CONFLICT on Testing apps.** OOB: "This deprecation is only applicable to production apps (i.e
  apps with publishing status set to In Production. The flow will continue to work for apps with
  the Testing publishing status." vs NATIVE "no longer supported" and OOB's own "January 31, 2023
  - all existing clients are blocked" [doc: OOB, NATIVE]. Irrelevant to the helper, which uses
  loopback; noted so nobody falls back to OOB.

### E.4 Custom URI schemes

- **CONFLICT on scope.** NATIVE, `redirect_uri` row: "Custom URI schemes are no longer supported on
  Android and Chrome apps." NATIVE, "App redirect methods": "Custom URI schemes are no longer
  supported due to the risk of app impersonation." [doc: NATIVE] No date is given for either
  **[not stated]**. Form when it applied: `com.example.app:redirect_uri_path`; "The custom scheme
  must contain a period to be valid." [doc: NATIVE]

## F. The redirect back to the listener

### F.1 On success

- The browser makes a GET to the redirect URI with the result as query parameters [example: WEB,
  OIDC; source: RFC6749 4.1.2]. NATIVE: "Regardless of the scheme, the response will either
  contain an authorization code (`code`) or an error (`error`)." [doc: NATIVE]
- Parameters:
  - `code`: always [doc: NATIVE]. Example value `4/P7q7W91a-oMsCeLvIaQm6bTrgtp7` [example: WEB,
    OIDC]: it contains `/` (unencoded in OIDC's example), so the listener should parse the query
    as form-encoded and not assume a character set [inferred]. Size: "Authorization
    codes" up to "256 bytes" [doc: OAUTH2]. Lifetime **[not stated]** by Google; RFC 6749:
    "A maximum authorization code lifetime of 10 minutes is RECOMMENDED." and "The client MUST NOT
    use the authorization code" more than once [source: RFC6749 4.1.2]
  - `state`: see headline 2 (fragment vs query) [doc: NATIVE, WEB, OIDC].
  - `scope`: in OIDC's example only:
    `...?state=security_token%3D138r5719ru3e1%26url%3Dhttps%3A%2F%2Foa2cb.example.com%2FmyHome&code=4/P7q7W91a-oMsCeLvIaQm6bTrgtp7&scope=openid%20email%20https://www.googleapis.com/auth/userinfo.email`
    [example: OIDC]. No page lists `scope` as a redirect parameter **[not stated]**; WEB's
    example has `code` alone [example: WEB].
  - `iss`: the discovery document says `authorization_response_iss_parameter_supported: true`
    [discovery]; no page read here describes an `iss` redirect parameter **[not stated]**.
  - Anything else (for example `authuser`, `prompt`, `hd`) **[not stated]**. "Per RFC 6749,
    clients MUST ignore unrecognized response parameters." [doc: OIDC]

### F.2 On refusal and other errors

- "`error=access_denied` indicates that the user declined the request." [doc: NATIVE]; WEB's
  example: `https://oauth2.example.com/auth?error=access_denied` [example: WEB].
- Whether `state` comes with an error: WEB: the `state` value is returned "after the user consents
  to or denies your application's access request" [doc: WEB]; RFC 6749 4.1.2.1 requires it
  [source: RFC6749].
- Other `error` values that can reach the redirect: RFC 6749 4.1.2.1 lists `invalid_request`,
  `unauthorized_client`, `access_denied`, `unsupported_response_type`, `invalid_scope`,
  `server_error`, `temporarily_unavailable`, with optional `error_description` and `error_uri`
  [source: RFC6749]. Google names none of these as redirected values except `access_denied`
  **[not stated]**.
- **Errors shown to the person instead of redirected** [doc: NATIVE, step 3 "Errors"]: "Requests
  to Google's OAuth 2.0 authorization endpoint may display user-facing error messages instead of
  the expected authentication and authorization flows."
  - `admin_policy_enforced`: "The Google Account is unable to authorize one or more scopes
    requested due to the policies of their Google Workspace administrator."
  - `disallowed_useragent`: an embedded user-agent (not this helper's case).
  - `org_internal`: the client's project limits access "to Google Accounts in a specific Google
    Cloud Organization" (an Internal app used by an outside account).
  - `deleted_client`: "The OAuth client being used to make the request has been deleted."
  - `redirect_uri_mismatch`: "The `redirect_uri` passed in the authorization request does not match
    an authorized redirect URI for the OAuth client ID."; "The passed `redirect_uri` may be invalid
    for the client type."
  - `invalid_request`: badly formatted, missing required parameters, an unsupported method, or an
    unsupported custom scheme.
  - RFC 6749 4.1.2.1 agrees for redirect URI and client errors: the server "MUST NOT
    automatically redirect the user-agent to the invalid redirection URI" [source: RFC6749].
- A test user missing from the list, a non-test user on a "Testing" client, or a person who
  closes the tab: what the browser shows, and whether anything is redirected, is
  **[not stated]**. QUESTION 3.

### F.3 What the listener should answer

- "When your app receives the authorization response, for best usability it should respond by
  displaying an HTML page that instructs the user to close the browser and return to your app."
  [doc: NATIVE]
- WEB, for a web server: "If your response endpoint renders an HTML page, any resources on that
  page will be able to see the authorization code in the URL." and "we recommend that the server
  first handle the request, then redirect to another URL that doesn't include the response
  parameters." [doc: WEB] A page with no external resources avoids the first [inferred].
- Confirm `state` before using the code: "you should confirm that the `state` received from Google
  matches the `state` sent in the authorization request." [doc: WEB]; "The `state` parameter should
  be a unique, non-guessable value generated by your application." [doc: BEST]
- Status code, content type and what to do with requests for other paths (for example
  `/favicon.ico`) are **[not stated]**.
- **[cairn]** The listener answers every request with one of three fixed pages, none with a
  resource or a value from the request: the answer (200, close the tab), a refusal with the
  right `state` (200, nothing to keep, close the tab), and anything else (another path, method
  or `state`, an over-long or malformed request: a 4xx status, "not the answer"). Each is sent with `Cache-Control: no-store`
  and `Referrer-Policy: no-referrer`.

## G. The code exchange

`POST https://oauth2.googleapis.com/token`, body `application/x-www-form-urlencoded`
[doc: NATIVE; example: NATIVE]. Same URL in the discovery document's `token_endpoint` [discovery].

### G.1 Request fields

[doc: NATIVE, step 5]

| Field | NATIVE says |
|---|---|
| `client_id` | "The client ID obtained from the Cloud Console Clients page." |
| `client_secret` | "Optional". "The client secret obtained from the Cloud Console Clients page." |
| `code` | "The authorization code returned from the initial request." |
| `code_verifier` | "The code verifier you created in Step 1." |
| `grant_type` | "must be set to `authorization_code`" |
| `redirect_uri` | "One of the redirect URIs listed for your project in the Cloud Console Clients page for the given `client_id`." |
| `DPoP` (header) | Optional; "If provided, the returned tokens are bound to this key." |

[example: NATIVE; the DPoP proof is shortened here]
```
POST /token HTTP/1.1
Host: oauth2.googleapis.com
Content-Type: application/x-www-form-urlencoded
DPoP: eyJ0eXAiOiJkcG9wK2p3dCIs...

code=4/P7q7W91a-oMsCeLvIaQm6bTrgtp7&
client_id=your_client_id&
redirect_uri=http://127.0.0.1:9004&
grant_type=authorization_code
```

- **The sample omits `client_secret` and `code_verifier`**, though the table lists the verifier
  without "Optional" [doc and example: NATIVE]. Whether the exchange fails without each is
  **[not stated]**. OIDC's table lists `client_secret` without "Optional" and its example sends it
  [doc and example: OIDC, web clients]. The discovery document's
  `token_endpoint_auth_methods_supported` is `["client_secret_post", "client_secret_basic"]`
  (no `none`) [discovery]: in the body, as in NATIVE's table, or as HTTP Basic. QUESTION 4.
- **`redirect_uri` must equal the one sent in the authorization request:** Google says only "One of
  the redirect URIs listed for your project" [doc: NATIVE]; RFC 6749 4.1.3: "their values MUST be
  identical" [source: RFC6749]. With a random port, that means the same port [inferred].
- Request encoding: RFC 6749 4.1.3: "application/x-www-form-urlencoded" format "with a character
  encoding of UTF-8" [source: RFC6749]. NATIVE's example shows the code's `/` unencoded
  [example: NATIVE].
- DPoP: optional and "recommended for increased security" [doc: NATIVE]; BEST: "Adopting DPoP is
  strongly recommended if your application:" first case "Operates as an installed or desktop
  application" [doc: BEST]. A DPoP-bound refresh token needs a DPoP proof on every refresh, signed with the same
  key [doc: NATIVE; google-auth.md B.2], which an `authorized_user` file cannot hold
  [inferred]. QUESTION 9.

### G.2 Response

"A successful exchange is indicated by a `200 OK` response containing the tokens." [doc: NATIVE]

| Field | NATIVE says |
|---|---|
| `access_token` | "The token that your application sends to authorize a Google API request." |
| `expires_in` | "The remaining lifetime of the access token in seconds." |
| `id_token` | "This property is only returned if your request included an identity scope, such as `openid`, `profile`, or `email`." A JWT. |
| `refresh_token` | "Refresh tokens are valid until the user revokes access or the refresh token expires." "Note that refresh tokens are always returned for installed applications." |
| `refresh_token_expires_in` | "The remaining lifetime of the refresh token in seconds. This value is only set when the user grants time-based access." |
| `scope` | "The scopes of access granted by the `access_token` expressed as a list of space-delimited, case-sensitive strings." |
| `token_type` | "This value is always `Bearer`, even when DPoP is used." |

[example: NATIVE; this example is the DPoP case, hence the `DPoP-Nonce` header]
```
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
DPoP-Nonce: AN3XwJjZsjnb0ZuWkRlek8QU7wY-Zhf-5IP6tO0tORz0KgtDT1Bo8FX-w4nz3r5lnepI

{
  "access_token": "1/fFAGRNJru1FTz70BzhT3Zg",
  "expires_in": 3920,
  "token_type": "Bearer",
  "scope": "https://www.googleapis.com/auth/drive.metadata.readonly https://www.googleapis.com/auth/calendar.readonly",
  "refresh_token": "1//xEoDL4iW3cxlI7yDbSRFYNG01kVKM2C-259HOF2aQbI"
}
```

- "Note: Your application should ignore any unrecognized fields included in the response."
  [doc: NATIVE]
- Sizes: access tokens up to 2048 bytes, refresh tokens up to 512 bytes; "Google reserves the
  right to change token size within these limits" [doc: OAUTH2].
- Granted scopes may be fewer than requested: "users may not grant your app access to all of them.
  Your app must verify which scopes were actually granted" [doc: NATIVE, step 6]. GRANULAR: with
  one non-Sign-In scope "users either approve or deny the entire request, and there is no checkbox
  in the consent screen"; with "2+" non-Sign-In scopes the granular screen is "Applicable"
  [doc: GRANULAR]. Also: "The scope included in your request may not match the scope included in
  your response, even if the user granted all requested scopes." [doc: OAUTH2]
- Store both tokens "in a secure, long-lived location" [doc: NATIVE].

### G.3 What the helper writes: an `authorized_user` file

- No page in the NATIVE set defines the `authorized_user` format **[not stated]**. WS-QUICK's
  sample writes `creds.to_json()` to `token.json` and reads it back with
  `Credentials.from_authorized_user_file` [example: WS-QUICK]; the file's content is not shown.
- AIP-4113 shows gcloud's file [example: AIP-4113]:
  ```
  {
   "client_id": "fake_id.apps.googleusercontent.com",
   "client_secret": "fake_secret",
   "quota_project_id": "fake_project",
   "refresh_token": "fake_token",
   "type": "authorized_user"
  }
  ```
  "All the fields are populated by the login response from the Google authorization backend except
  for 'quota_project_id' which is retrieved from gcloud's context." and the refresh goes to
  `https://oauth2.googleapis.com/token` with "the refresh token, client ID and client secret"
  [doc: AIP-4113]. `quota_project_id` sets the `X-Goog-User-Project` header [doc: AIP-4113].
- cairn reads `type`, `client_id`, `client_secret`, `refresh_token` and rejects a document
  without a `client_secret` (`src/cairn/sources/google_auth.py`, `_authorized_user`;
  google-auth.md question 6) [cairn code, read 2026-11-14].
- **[cairn]** `google-authorize` writes exactly `type`, `client_id`, `client_secret` and
  `refresh_token` on one line, with no `quota_project_id`, to a new file created with mode 0600
  and never overwritten.

## H. Errors

### H.1 At the authorization endpoint

See F.2: shown to the person, except `access_denied`, which is redirected.

### H.2 At the token endpoint (code exchange)

- Google's list for the code exchange, WEB only (web clients): `invalid_grant`: "The supplied
  authorization code is invalid or in the wrong format. Request a new code by restarting the OAuth
  process to prompt the user for consent again." [doc: WEB]
- NATIVE's `invalid_grant` entry (under step 3): "If you are using a code verifier and challenge,
  the `code_challenge` parameter is invalid or missing." and the refresh case [doc: NATIVE].
- `invalid_client`: "The OAuth client secret is incorrect. Review the OAuth client configuration,
  including the client ID and secret used for this request." [doc: WEB only; NATIVE does not list
  it]
- DPoP failures: "`400 Bad Request`"; `invalid_dpop_proof`, `use_dpop_nonce`; "Other failures
  might return `invalid_grant`." [doc: NATIVE]
- **Status and body: [not stated] by Google** for anything but DPoP (also google-auth.md
  headline 1). RFC 6749 5.2 [source: RFC6749]: "HTTP 400 (Bad Request) status code (unless
  specified otherwise)"; JSON with `error` (required), `error_description` and `error_uri`
  (optional); `invalid_client` may be 401; codes `invalid_request`, `invalid_client`,
  `invalid_grant`, `unauthorized_client`, `unsupported_grant_type`, `invalid_scope`. Example:
  ```
  HTTP/1.1 400 Bad Request
  Content-Type: application/json;charset=UTF-8
  Cache-Control: no-store
  Pragma: no-cache

  {
    "error":"invalid_request"
  }
  ```
- A code exchanged twice, an expired code, a `redirect_uri` differing from the authorization
  request's, a wrong `code_verifier`: RFC 6749 5.2 and RFC 7636 4.6 put all under `invalid_grant`
  [source: RFC6749, RFC7636]; Google **[not stated]**. Whether `redirect_uri_mismatch` can also come
  from the token endpoint is **[not stated]**; NATIVE describes it for "the authorization request".

## I. What decides how long the refresh token lives

Not interpreted. Passages from developers.google.com pages (OAUTH2, POLICIES, WS-CONSENT) are
quoted; those from the Cloud Help Center (H-*) are paraphrased in our words. The token side (six months unused, revocation, the 100-token limit) is in
google-auth.md B.4.

### I.1 User type

- External (paraphrased): anyone with a Google Account can use the project, and which of the
  app's scopes a person can authorize depends on the project's publishing status. [doc:
  H-AUDIENCE]
- Internal (paraphrased): a project that belongs to a Google Cloud Organization can set Internal
  users, which limits authorization requests to the organization's members; a person outside
  the project's parent who asks to authorize is shown an `org_internal` error. [doc: H-AUDIENCE]
  Authorizing scopes of restricted Google Workspace services, the high-risk Gmail and Drive
  scopes among them, may need further configuration by the organization's administrators
  (paraphrased). [doc: H-AUDIENCE]
- Internal apps need no verification (paraphrased): only people in the project's Google Workspace
  or Cloud Identity organization use the app, the organization owns the project, and its OAuth
  Consent Screen is set for internal use; an app set as internal-only meets neither the
  unverified app screen nor the 100-user cap. [doc: H-NOTNEEDED]

### I.2 Publishing status "Testing"

- A project in Testing is limited to at most 100 test users, listed on the OAuth consent screen
  (paraphrased). [doc: H-AUDIENCE]
- Google shows a listed test user a warning before that user can authorize the scopes the
  project's OAuth clients ask for (paraphrased). [doc: H-AUDIENCE]
- **The 7-day rule, two wordings:**
  - H-AUDIENCE (under "Testing", not tied to a user type; paraphrased): a test user's
    authorization ends seven days after consent, and a refresh token the client received by
    asking for `offline` access ends with it.
  - OAUTH2: "A Google Cloud Platform project with an OAuth consent screen configured for an external
    user type and a publishing status of" Testing "is issued a refresh token expiring in 7 days,
    unless the only OAuth scopes requested are a subset of name, email address, and user profile"
    [doc: OAUTH2; the page puts Testing in quotation marks].
  - **CONFLICT on the condition**: OAUTH2 ties it to "an external user type"; H-AUDIENCE does not
    name the user type. Whether an Internal app in Testing gets 7-day tokens is **[not stated]**
    clearly. QUESTION 5.
- Exception (paraphrased): when only name, email address and user profile are asked for (the
  `userinfo.email, userinfo.profile, openid` scopes or their OpenID Connect equivalents), the
  authorizations do not end after 7 days [doc: H-AUDIENCE]. cairn's scopes are not of this
  kind [inferred].
- A test user may be unable to authorize the scopes the project's OAuth clients ask for, because
  of which Google services the account has or of restrictions set on it (paraphrased). [doc:
  H-AUDIENCE]

### I.3 Publishing status "In production"

- A project In production is open to anyone with a Google Account; the status becomes In
  production once the Publish app button is pressed (paraphrased). [doc: H-AUDIENCE]
- The project's configuration may have to be verified before its name and logo show on an
  authorization screen, or before it may ask for sensitive or restricted scopes (paraphrased).
  [doc: H-AUDIENCE]
- Google shows an Unverified apps warning when the project's OAuth clients ask for sensitive or
  restricted scopes before the project's verification for those scopes is done (paraphrased).
  [doc: H-AUDIENCE]
- **User cap** (paraphrased): the cap limits how many people can grant the app unapproved
  sensitive or restricted scopes; it counts over the project's whole lifetime and can be neither
  reset nor changed. The table's row for apps that show people the unverified app screen gives
  100 new users in total, counted from when the app shows that screen. [doc: H-AUDIENCE,
  H-UNVERIFIED] H-FAQ: using it up can lead to Google sign-in being switched off for the app's
  users (paraphrased) [doc: H-FAQ].
- No page states a 7-day or other fixed expiry for refresh tokens of an "In production" app; the
  general reasons in google-auth.md B.4 apply **[not stated otherwise]**.
- Scope classes and verification: an app that asks for scopes classed as sensitive or restricted
  must pass Google's OAuth app verification before it is granted them (paraphrased). [doc:
  H-VERIFY] WS-CONSENT's table (its cells are icons, read from the icon names `check` and `—` in
  the text): non-sensitive needs basic verification; sensitive adds "Additional app verification
  required"; restricted adds "Security assessment required" [doc: WS-CONSENT]. H-REQS: sensitive
  and restricted scopes need brand verification (a homepage on a verified domain the developer
  owns, a privacy policy, domain ownership in Search Console) plus a demonstration video, scope
  justification and Limited Use; restricted scopes add a security assessment every year by one of
  Google's empanelled security assessors (paraphrased) [doc: H-REQS]; the assessment uses CASA
  and every application is revalidated each year (paraphrased). [doc: H-CASA]
- Rate of new users: H-PLATFORM names a token grant rate limit that bounds how fast an
  application can gain new users (paraphrased) [doc: H-PLATFORM]; the "OAuth Application Rate
  Limits" page it links to was not fetched.
  Apps that ask for restricted scopes go through verification again every year (paraphrased)
  [doc: H-VERIFY]. Restricted Gmail scopes include `gmail.readonly`; restricted Drive scopes include
  `drive.readonly` and `drive.meet.readonly` [doc: H-RESTRICTED]. Classes of cairn's other scopes:
  see the per-API notes.

### I.4 When verification is not needed

[doc: H-NOTNEEDED, "When is verification not needed"]

- Personal Use apps (paraphrased): an app for the developer's personal use (fewer than 100 users)
  can go on being used by the developer and those few users without verification, the users
  clicking through the unverified app warning screens at sign-in.
- Development/Testing/Staging apps (paraphrased): an app in development, testing or staging
  does not have to go through verification, but meets the unverified app screen, and the 100-user cap applies to it.
- Internal use apps (I.1); admin-trusted or Marketplace-installed apps.
- POLICIES defines "production" by exclusion: "It isn't for personal use. An app is considered to
  be for personal use if it's not shared with anyone else or will be used by fewer than 100 people
  (all of whom are known personally to you)." and "It isn't for internal use" [doc: POLICIES].
- H-UNVERIFIED (older wording, paraphrased): an app in development, experimental or a test
  build, needs no verification until it is launched to the public; and Internal apps, for users
  of one G Suite domain [doc: H-UNVERIFIED].

### I.5 A personal Google account's own project

- Not stated as a case anywhere **[not stated]**. What the pages give: Internal needs a project
  that belongs to a Google Cloud Organization [doc: H-AUDIENCE]; so a project without an
  organization can only be External [inferred]. External + Testing gives 7-day tokens (I.2).
  External + In production, unverified, with sensitive or restricted scopes, gives the unverified
  app screen that the person clicks through, inside the 100-user cap, and no stated 7-day expiry
  (I.3, I.4) [inferred from the quotes]. Whether restricted scopes (Gmail, Drive) can be granted at
  all by an unverified "In production" app for personal use is **[not stated]** beyond
  H-NOTNEEDED's "Personal Use apps" sentence. QUESTION 5.

## J. What the person sees and must do

1. The person opens the authorization URL in a browser **on the machine where the helper's
   listener runs**: the redirect goes to `127.0.0.1`/`[::1]`, that is to the browser's own machine
   [inferred from E.1]; RFC 8252 8.3: "the HTTP request never leaves the device" [source:
   RFC8252]. Sending the URL to a person on another computer fails unless the operator forwards
   the port [inferred; not stated]. NATIVE: "desktop apps must open the system browser"; an
   embedded browser is refused (`disallowed_useragent`; POLICIES "Use secure browsers") [doc:
   NATIVE, POLICIES]. The helper never opens one itself. The flow for apps without a local
   browser, DEVICES, "is supported only for the following scopes": `email`, `openid`, `profile`,
   `drive.appdata`, `drive.file`, `youtube`, `youtube.readonly` [doc: DEVICES], none of cairn's
   four (`gmail.readonly`, `drive.readonly`, `calendar.events.readonly`, `meetings.space.readonly`),
   so it is no fallback. QUESTION 0.
2. Sign in and pick the account; `login_hint` can prefill it (D.1). [doc: NATIVE]
3. **Unverified app screen** (sensitive or restricted scopes, unverified): the app may show an
   unverified app screen before the consent screen (paraphrased). [doc: H-UNVERIFIED];
   for a test user in Testing, a warning that says the person has test access to the project and
   should weigh the risk of granting an unverified app access to their data (paraphrased)
   [doc: H-AUDIENCE].
4. **Consent screen**: "Google displays a consent window that shows the name of your application and
   the Google API services that it is requesting permission to access" [doc: NATIVE]. But the OAuth
   consent screen shows the app's name only once the app is verified, and before that people see
   only the application's domain (paraphrased). [doc: H-BRANDING] What an unverified Desktop app with no domain shows instead **[not stated]**.
5. With one non-Sign-In scope, accept or refuse the whole; with two or more, per-scope checkboxes
   [doc: GRANULAR].
6. The browser lands on `http://127.0.0.1:<port>/...`; the listener's page says to close the tab
   (F.3).
7. Before any of this, while the client is "Testing": the operator must have added the person's
   address under Audience > Test users [doc: WS-CONSENT, H-AUDIENCE]. A Brand Account can authorise
   when a listed test user manages it (paraphrased) [doc: H-AUDIENCE].
8. A Workspace account may be blocked by its admin (`admin_policy_enforced`) [doc: NATIVE, OAUTH2].
   An account enrolled in Advanced Protection may block most apps not made by Google
   (paraphrased). [doc: H-AUDIENCE]
9. Revoking later: "A user can revoke access by visiting Account Settings." [doc: NATIVE]

## K. Policy (quoted, not interpreted)

- "You must create a separate OAuth client for each platform on which your app will run" and "You
  must choose the client type that best matches the platform." [doc: POLICIES] OAUTH2: "For
  Desktop apps (such as Windows, macOS, or Linux applications or CLI tools), use the Desktop app
  application type." [doc: OAUTH2]
- "Treat your OAuth client credentials with extreme care, as they allow anyone who has them to use
  your app's identity to gain access to user information." [doc: POLICIES]
- "Never transmit tokens in plaintext, and always store encrypted tokens at rest to provide an
  extra layer of protection in the event of a data breach." [doc: POLICIES]
- "you must not use, or encourage the use of, user credentials for server-to-server deployment."
  in the context of Cloud session control policies [doc: OAUTH2; google-auth.md B.4]. CONFLICT in
  spirit with this helper's purpose (a refresh token used by a server); the sentence sits under
  "Dealing with session control policies for Google Cloud Platform (GCP) organizations" and its
  example is "any third party OAuth application that requires the Cloud Platform scope" [doc:
  OAUTH2]. QUESTION 10.
- "Your project configuration in the Google Cloud Console Consent Screen page must match the list
  of scopes requested by your app." [doc: POLICIES]
- "you must create separate projects in the Google Cloud Console for each deployment tier, such as
  development, staging, and production." [doc: POLICIES]
- "Google reserves the right to delete unused OAuth clients if the client is inactive for at least
  6 months." [doc: POLICIES]
- Using the data: Limited Use and the Workspace user data policy are in the per-API notes
  (google-meet-api.md J); every app that works with Google APIs has to follow Google's API
  Services User Data Policy, verified or not (paraphrased). [doc: H-NOTNEEDED]

---

## Open questions for the helper

0. QUESTION: Where does the person's browser run? The loopback redirect reaches only the machine
   the browser is on, so the helper must run on the person's own computer (or the operator must
   forward the port to it), and the device flow does not allow cairn's scopes (J.1). This decides
   whether the helper is run by the operator on the server, or handed to the person.
   **[cairn]** `google-authorize` runs on the machine whose browser the person uses. It prints
   the address and never opens a browser itself; the person opens it there.
1. QUESTION: Does a Desktop client's code exchange return a `refresh_token` every time, or only the
   first time unless the request has `prompt=consent` (and does it need `access_type=offline`)?
   NATIVE: "always returned for installed applications" and no `access_type`/`prompt` in its table;
   OIDC: "only ... the first time"; NATIVE's refresh section: "if you requested offline access"
   (D.3, D.4, headline 1). The helper fails with nothing to write if the field is absent.
   **[cairn]** The request always has `access_type=offline` and `prompt=consent`. An answer
   without `refresh_token` ends with exit 1 and nothing written. To confirm with a real Google
   account: that a second authorisation by the same person, with `prompt=consent`, returns a
   refresh token again.
2. QUESTION: Is `state` returned in the query string or the fragment for a loopback redirect?
   NATIVE says fragment, WEB and OIDC say query; a listener only sees the query (headline 2, F.1).
   **[cairn]** `state` is read from the query only; a request whose query lacks it, or holds
   another, is not the answer, and the command goes on waiting. To confirm with a real Google
   account: that the loopback redirect brings `state` back in the query.
3. QUESTION: How long should the listener wait, and what should the helper tell the operator, when
   nothing arrives? Every authorization error except `access_denied` is shown to the person and
   not redirected; what a non-test user of a Testing client sees is not stated (F.2).
   **[cairn]** `--wait`, 300 seconds unless given, from 30 to 900. When nothing arrives, the
   command says nothing was written, that the browser may have shown one of
   `redirect_uri_mismatch`, `org_internal`, `deleted_client` or `admin_policy_enforced`, and
   that while the consent screen is "Testing" the person must be listed as a test user (J.7).
   It does not say what a person who is not a test user sees, which is not stated.
4. QUESTION: Is `client_secret` required at the code exchange and on refresh for a Desktop client?
   NATIVE says "Optional" and its sample omits it; the discovery document offers only
   `client_secret_post` and `client_secret_basic`; cairn's `authorized_user` loader requires one
   (B, G.1, G.3). Related: if the client file was downloaded without a secret, or the secret was
   lost after creation, the only remedy stated is rotation (B).
   **[cairn]** The client file must hold a `client_secret`, and the exchange always sends it in
   the form, as cairn's `authorized_user` loader requires one on every refresh.
5. QUESTION: Which consent-screen setting should the operator be told to use for long-lived tokens?
   External + Testing expires in 7 days; Internal needs an organization; External + In production
   without verification shows the unverified screen under a lifetime 100-user cap. Do restricted
   scopes (`gmail.readonly`, `drive.readonly`) work for an unverified personal-use app In production,
   and does the 7-day rule apply to an Internal app in Testing (OAUTH2 vs H-AUDIENCE wording)? (I)
   **[cairn]** The command chooses no setting. The sources guide quotes the 7-day rule for an
   External client in "Testing" and says Google's pages do not state whether a restricted scope
   works for an unverified personal client "In production"; it promises nothing. To confirm
   with a real Google account: whether `gmail.readonly` and `drive.readonly` can be granted to
   an unverified personal client "In production", and how long its refresh token lasts.
6. QUESTION: Exactly which loopback URIs does a Desktop client accept: any port (stated only by RFC
   8252), a path (`/callback`), a trailing slash, `[::1]` as well as `127.0.0.1`? The Console has no
   redirect URI field for a Desktop client, while NATIVE says the value must "exactly match" a
   configured one (E.1).
   **[cairn]** `http://127.0.0.1:<port>`, IPv4 only, no path and no trailing slash; the port is a
   free one unless `--port` is given. The listener binds 127.0.0.1 only. To confirm with a real
   Google account: that a Desktop client accepts any port without registering it.
7. QUESTION: Which `auth_uri`/`token_uri` will a current Desktop download contain, and should the
   helper use them or always use the endpoints of NATIVE and the discovery document? The only shown
   file has `accounts.google.com/o/oauth2/auth` and `.../o/oauth2/token` (B).
   **[cairn]** Always `https://accounts.google.com/o/oauth2/v2/auth` and
   `https://oauth2.googleapis.com/token`, whatever the file says, except that an `auth_uri` or a
   `token_uri` over http on 127.0.0.1 or localhost is used as it is, for tests and stand-ins.
8. QUESTION: What does a current Desktop client download contain beyond the five keys (for example
   `project_id`), and is its `redirect_uris` still `["http://localhost", ...]`? No Google page shows
   it (B).
   **[cairn]** Only `installed.client_id` and `installed.client_secret` are read (and the two
   endpoints, as in question 7); other keys are ignored, and a file with `web` is refused with a
   message to create a Desktop app client. To confirm with a real Google account: the keys of a
   current Desktop download.
9. QUESTION: Should the helper use DPoP? Google strongly recommends it for desktop apps, but a
   DPoP-bound refresh token needs the private key on every refresh, which the `authorized_user`
   document (client id, secret, refresh token) cannot carry (G.1).
   **[cairn]** No DPoP: the `authorized_user` document has no place for the key.
10. QUESTION: Does "you must not use, or encourage the use of, user credentials for server-to-server
    deployment" apply beyond the Cloud session-control case it is written under? The helper exists
    to put one person's refresh token on a server (K).
11. QUESTION: Should cairn's Desktop client be created with the checkbox "Use this client for an
    AI-powered agent", which cannot be changed afterwards, and "Some Google MCP endpoints" accept only such
    clients; no page says whether ordinary REST APIs treat it differently (A.4).
12. QUESTION: Does the authorization request without `code_challenge` fail for a Desktop client, and
    with which error? "Always use PKCE" vs "Recommended" (C).
   **[cairn]** PKCE is always sent, with `code_challenge_method=S256`.
13. QUESTION: What status and JSON body does the token endpoint return for a bad code, a reused
    code, a wrong verifier, a wrong `redirect_uri` and a wrong secret? Only RFC 6749 5.2 gives a
    shape (H.2).
   **[cairn]** Only the `error` code is read, whatever the status, and it chooses the line:
   `invalid_grant`, `invalid_client`, `redirect_uri_mismatch`, `invalid_scope` and
   `unauthorized_client` each have their own. Without one of those codes, the status chooses:
   a redirect, a 5xx (Google's fault) or another refusal, which names the status. An endpoint
   that cannot be reached, an answer over the size bound and a 200 that is not a JSON object
   each have their own line too. The response's text is never shown.
14. QUESTION: When several cairn sources share one client, should the helper ask for all their
    scopes at once (granular checkboxes; the person may grant some) or one scope per run (one
    refresh token per source, each counting toward the 100-per-client limit)? (G.2, D.4)
   **[cairn]** One run asks for the scopes of every `--kind` given, each once, and the answer's
   `scope` must hold all of them, in any order and with others; a kind whose scope is missing is
   named and nothing is written. To confirm with a real Google account: OAUTH2 says the
   response's scope "may not match" the request's even when all were granted (G.2); if Google
   leaves out a granted scope, this check refuses a good answer.
