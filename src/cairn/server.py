"""The cairn web service: REST API, MCP endpoint and web UI over one Store of many accounts.

Contract (frozen by tests/acceptance/test_w2_server.py; for tenancy by
tests/acceptance/test_h2_tenancy.py; for OAuth by tests/acceptance/test_h3_oauth.py and
tests/acceptance/test_h3b_oauth_strict.py; for
sign-in, people, keys in the UI and onboarding by tests/acceptance/test_h4_signin.py; for
connections limited to chosen projects and project sets by tests/acceptance/test_s1_scopes.py;
for project kinds and the guided setup by tests/acceptance/test_u1_setup.py; for sources by
tests/acceptance/test_c2_langfuse.py; for a workspace's export and deletion by
tests/acceptance/test_h5_data.py; for members, roles and who reaches which project by
tests/acceptance/test_t1_members.py; for plans, caps and usage by
tests/acceptance/test_h6_quotas.py; for
deleting a project and forgetting a note by tests/acceptance/test_h5b_forget.py):

Tenancy
- A request acts for exactly one account: the account of the API key it presents
  (`Store.resolve_key`), of the OAuth access token it presents on /mcp
  (`Store.resolve_token`), or of the session cookie it carries (`Store.resolve_session`). A
  session belongs to the account of the key that was used to log in, or to the workspace a
  person signed in to; a token to the account that approved its client, and to the person
  who did when one had signed in. A person who is no longer a member of that workspace is
  refused like a wrong credential.
- REST, the MCP tools and the web UI only ever read and write that account's data. Another
  account's project, page or claim, named by id or by name, is answered exactly like one that
  does not exist: 404 over REST and in the UI, a tool error over MCP, with the same message
  an unknown id gets.
- Project names are per account: two accounts can each have a project named "core".
- `recall` and search without a project cover every project of the caller's account, and nothing
  else.
- This holds when requests from different accounts run at the same time.
- With only the default account, everything below behaves as it did in v0.
- Reach. A request made with an API key or an OAuth access token is served from its
  connection's narrowed view (`Store.narrow` with the Principal's `projects` and `access`):
  a project out of reach, by id or by name, is answered exactly like one that does not exist
  (404 over REST and a tool error over MCP, with the message an unknown project gets), and
  `recall` and search without a project cover only the reach, and their scores depend only on
  the projects in reach (`Store.search`). A write through a read-only
  connection is 403 over REST and a tool error over MCP, saying "read-only"; creating a
  project through a connection limited to named projects is refused the same way, saying
  "limited", whatever the name. Web sessions keep the whole workspace, so POST /login with
  a key that is limited or read-only is refused like a wrong key (401), and no session is
  made.
- How: `Accounts` is the only holder of the unscoped store, and this module is the only
  place where a credential becomes a Principal. For each request, Auth has `Accounts` turn
  the credential into a Principal and the Principal into a `Memory` over
  `Store.for_account` (acting for the Principal's person, when it has one) narrowed to the
  Principal's reach, and puts that Memory on the request's ASGI scope. REST and UI handlers
  read it from their request. The MCP tools are registered once and run on worker threads,
  so each reads it from the HTTP request the SDK hands that call
  (`Context.request_context.request`). Nothing is kept between requests, and a request
  without a Memory is an error, never the default account. The OAuth endpoints, which work
  before there is an account, get an `oauth.Ledger` from `Accounts`: the store's operations
  on clients, codes and tokens, and nothing of any account's data.

Auth (Starlette middleware on the whole app, so it also covers the mounted MCP app)
- Public: GET /healthz, GET and POST /login, /static/*, every /.well-known/* path, the
  OAuth endpoints /authorize, /token, /register and /revoke, and the sign-in routes under
  /auth/ (404 without sign-in).
- /api/*: an `x-api-key` header or `Authorization: Bearer <key>` that Store.resolve_key
  knows; otherwise 401 with a JSON error body.
- /mcp: the same, or, when OAuth is on, `Authorization: Bearer <access token>` that
  Store.resolve_token knows; otherwise 401 with a JSON error body. A token is recognised by
  its prefix, which no key can have, so a bearer value is looked up once. A token is a
  credential on /mcp only, and only in the Authorization header.
- A path outside /api and /mcp that no route serves, for any method, is 404 (the error
  page, "There is no page at this address.") for everyone, signed in or not, and sets
  no cookie: it is no page, so there is nothing to sign in for. A signed-in reader gets
  it with the app's navigation, anyone else with the visitor's. A path that is a page for
  another method is not one of them.
- /docs and /docs/<name> (P1, cairn/web/public.py) open without a session, and
  a signed-in reader sees them with the app's navigation.
- Every other path is the web UI: a session cookie that Store.resolve_session knows
  (`__Host-cairn_session` on https, `cairn_session` over plain http; under a brand
  `__Host-session` and `session`: auth.branded_cookie);
  otherwise 303 to /login (to /login?next=<path and query> from GET /new, the share target,
  so what was shared survives signing in, from GET /join/<token>, an invitation, and from
  /oauth/consent as below). POST /login
  with form field `key` creates a session in that key's
  account (cookie flags: HttpOnly, SameSite=Strict, Path=/, and Secure only when public_url
  starts with https://) and redirects 303 to the form's `next` when that is a path on this
  server (it starts with one "/", and is printable ASCII without a backslash), else to /; a
  wrong key gives 401 and the form again, `next` kept. GET /login?next=... puts `next` in
  the form. POST /logout ends that session, and the session of any other cookie that
  browser sends under a name the session cookie has had (auth.SESSION_COOKIES; B1), and no
  other. With sign-in on, see "Sign-in".
- A UI form posted from another origin is refused with 403 (/login and /oauth/consent
  included). /api, /mcp, /register, /token and /revoke take no cookie and are exempt.
- A credential whose account no longer exists is refused like a wrong one.
- No response ever contains an API key, a session token or an OAuth token, except the
  Set-Cookie header, the token endpoint's answer to the client that earned it, and the
  answer to POST /keys, which shows the key it made once.
- GET requests and read-only MCP tools never write to the database. That covers validating
  a token, GET /authorize, GET /oauth/consent, the metadata documents, GET /agents and
  starting a sign-in. The one exception is the sign-in callback, and, with `serve
  --session-days`, the one guarded UPDATE that renews a session at most once a day (B1,
  "A wall, a brand").

OAuth 2.1 for /mcp (cairn is its own authorization server; cairn/oauth.py serves this)
- On when `public_url` is a bare origin (no path) over https, or over http on localhost,
  127.0.0.1 or [::1]. The issuer is that origin, and the one protected resource is the
  issuer + "/mcp". Otherwise every OAuth path answers 404, a token is not a credential, and
  API keys are the only way in.
- /mcp without valid credentials: 401 with `WWW-Authenticate: Bearer
  resource_metadata="<issuer>/.well-known/oauth-protected-resource/mcp"`, and
  `error="invalid_token"` before it when a credential was presented.
- Discovery, public and read-only: GET /.well-known/oauth-protected-resource/mcp and
  GET /.well-known/oauth-authorization-server (authorize, token, register and revoke
  endpoints at the root; PKCE S256; clients authenticate with "none"; one scope, "cairn").
- POST /register (RFC 7591), unauthenticated and therefore bounded. Every client is public:
  whatever authentication method it asks for it is given "none" and no secret, and whatever
  scope it asks for it is given "cairn". A redirect URI must be https, or http on localhost,
  127.0.0.1 or [::1], written plainly: no userinfo, no fragment, no other spelling of a
  host (`oauth.redirect_uri_problem`); anything else is 400, and so is one bad URI among
  good ones. Bounds: a 64 KiB body, 1 to 10 redirect URIs of at most 255 characters, a name
  cut to 80 characters after control and format characters are removed, and 20 stored
  registrations per client address per hour, or the number `serve --registrations-per-hour`
  and create_app's `registrations_per_hour` give (then 429; an IPv6 /64 is one address; the
  address is the peer's, or the client a `serve --trusted-proxy` peer names in
  X-Forwarded-For). A client that got no grant within 7 days of registering is removed
  by a later registration. Sign-in through a provider is limited the same way: 30 starts
  and, separately, 30 callbacks per client address in any minute (cairn.web.signin.SIGN_INS),
  then 429 with Retry-After on the sign-in page, nothing settled and no provider called.
  Whoever asks, at most 600 token exchanges with the providers in any minute
  (cairn.web.signin.PROVIDER_CALLS); a callback past that is answered the same way, and a
  warning is logged at most once a minute. A full per-address table forgets the address
  seen longest ago; it never refuses a new one for being full.
- GET /authorize (GET only) validates the request and redirects to
  GET /oauth/consent?request=<the request, sealed>; neither writes. The MCP SDK checks the
  client, the redirect URI and PKCE S256; cairn refuses a `resource` that is not this
  server's /mcp (`invalid_target`) and a challenge that is not a SHA-256 digest. The
  redirect URI is matched as a URL: another spelling of a registered URI (an upper-case
  host, the default port) is that URI, and the browser is always sent to the URI as it was
  registered (as POST /register answered with it); one that is another address once read
  ("/cb/../other", "/cb/") is 400 with no redirect. A parameter named twice is 400 with no
  redirect and no consent page, whatever the two values, except `resource` (RFC 8707),
  which may repeat when every value is this server's /mcp (else 400 `invalid_target`, no
  redirect). A refusal answered without a redirect (an unknown client, a redirect URI that
  is not the client's) is JSON with an `error` and a fixed description, the same for both,
  and nothing the request sent, `state` included; a refusal redirected to the verified
  redirect URI carries `error` and `state`. Requested scopes are not read. The sealed
  request is signed with a secret made at start-up and lasts 10 minutes: a restart only
  makes a pending approval fail.
- /oauth/consent needs a web session. Without one, GET answers 303 to
  /login?next=<its own path and query>, except when the browser says the navigation came
  from another site (`Sec-Fetch-Site: cross-site`): then it answers 200 with a
  `<meta http-equiv="refresh">` to the same URL, so the retry carries the SameSite=Strict
  session cookie if there is one. The page names the client (a name is the client's own
  claim), shows its redirect URI in full and the account that will be shared, and carries
  the request in the hidden field of one form posting to /oauth/consent with a `decision`
  (the page also names the person when one signed in)
  of "approve" or "deny". Its Content-Security-Policy allows form-action 'self' plus the
  redirect URI's origin, nothing wider.
- The consent page also asks what the agent will reach: the workspace's projects and sets as
  checkboxes named `projects` (a set shows the projects it holds), `scope` "all" ("All
  projects", checked) or "selected" ("Selected projects"), and `access` "write" ("Read and
  write", checked) or "read" ("Read only").
- POST /oauth/consent. Approve: 303 to the redirect URI with `code` and `state`, the code
  carrying the chosen reach: without `scope`, `projects` and `access`, all projects, read and
  write. "selected" with nothing ticked or with an id that is not one of the workspace's
  projects or sets, projects ticked without "selected" (the page has no script, so that is a
  person who ticked projects and left All projects chosen), or another `scope` or `access`, is
  400 and grants nothing; it is checked before the request is settled, so the same page can
  still be answered. Deny: 303 to it with `error=access_denied` and `state`. The form only
  counts from the account that was shown the page, and a request is decided once, however
  many times its page was opened; anything else is 400 and no redirect. A grant keeps its
  reach across token refreshes.
- POST /token. A code lasts 2 minutes and works once, only for its client and with its PKCE
  verifier; presenting it again ends the grant it made. A request whose `resource` (any
  value of it, when there are several) is not this server's /mcp is 400 `invalid_target`
  before anything is read or written; without `resource` it is this server's /mcp. A field
  named twice, other than `resource`, is 400 `invalid_request` and does nothing (RFC 6749,
  3.1). A `code_verifier` that is not 43 to 128 characters of A-Z a-z 0-9 - . _ ~
  (RFC 7636, 4.1) is 400 `invalid_request` before the code is looked up: the same answer
  whether the code exists or not, and the code stays good for its real verifier. An unknown
  code, a wrong verifier (the code is not used up) and another client's code get one
  answer, 400 `invalid_grant` with the same description; no answer from /token repeats what
  the client sent (a grant type, a scope). Expiry is judged when the write happens: the
  store reads the time once it holds the database's write lock, so a code or refresh token
  that expires while its exchange waits for the lock is expired, and nothing is issued or
  rotated.
- A code and the grant it makes are bound to the /mcp address the person approved at (the
  audience). A code, a refresh token or an access token of a grant bound to another address
  is unknown here (400 `invalid_grant`, or 401 at /mcp) and nothing is written, so moving
  `public_url` ends every grant made at the old one. The answer is an opaque access
  token (1 hour) and a refresh token (30 days), scope "cairn". A refresh rotates both and
  starts a new 30 days. A replaced refresh token is remembered for 30 days from its
  rotation, the life it would have had, and presenting any remembered one is refused and
  ends the whole grant, however many rotations back it is: a thief who copied a token and
  rotates first, however often, is caught when the client next refreshes. A token rotated
  more than 30 days ago is unknown: 400 `invalid_grant`, nothing is written, and the grant
  stays. A grant rotates at most 6 times in any hour (cairn.store.grants.ROTATIONS, counted
  from the database inside the rotating write, so it holds across restarts); past that a
  refresh answers 429 with Retry-After and nothing is written, so the tokens in hand keep
  working. So one grant remembers at most 6 x 24 x 30 = 4,320 rotated tokens.
- POST /revoke (RFC 7009): a public client sends `token` and `client_id`. Any token of one
  of its grants ends that grant; an unknown token is 200 too. A field named twice is 400
  `invalid_request`, and nothing is revoked.
- /register, /token and /revoke answer cross-origin requests (CORS, any origin, no
  credentials).
- GET /agents (web session) lists the account's connected agents by client name, each with
  its reach by name ("All projects", or the names of the projects and sets it still reaches) and
  its access ("Read and write" or "Read only"); POST /agents/<grant id>/revoke ends one,
  and its tokens stop working at once (404 for an unknown id or another account's).
- Deleting an account ends its grants. Tokens are stored as SHA-256 digests only.
- A grant approved by a signed-in person acts for that person: `Store.resolve_token` returns
  their `user_id`.

Sign-in (cairn/web/signin.py; the providers are cairn/identity.py)
- On with `create_app(..., sign_in=SignIn(providers, http, join="personal",
  allowed_email_domains=(), allowed_emails=()))`; without it, people sign in with an API key
  as above. It needs a `public_url` that turns OAuth on (a bare https origin, or http on a
  loopback host), at least one provider, unique provider names and a `join` of "personal" or
  "default" (ValueError otherwise). A shared workspace is never open to everyone:
  `join="default"` with a provider anyone can get an account at (GitHub, Google) and neither
  `allowed_email_domains` nor `allowed_emails` is a ValueError.
- `allowed_emails` (frozen by tests/acceptance/test_h8_allowed_emails.py): when not empty, a
  person signs in only when the email the provider verified is one of these addresses: the
  whole address, ignoring case and the spaces around an entry. No pattern, no sub-address,
  no address the provider lists beside the primary one; the same address twice is one entry.
  An entry that is not one address (blank, no "@" or more than one, nothing before or after
  it, a space inside) is a ValueError naming its position, never its text. With
  `allowed_email_domains` too, either admits; the domain rule is unchanged. It is checked at
  every sign-in before anything is written (an invitation does not open it), gates sign-in
  only (an open session, a key and an approved agent go on working), and is left out of
  `repr(SignIn)`. A refusal by the list alone says "This install admits only the people its
  operator has listed."; with domains set, the domain sentence stays. No refusal, page or
  log line repeats an address.
- Providers: `GitHub(client_id, client_secret)`, `Google(client_id, client_secret)` and
  `OIDC(name, label, issuer, client_id, client_secret, groups_claim=None,
  allowed_groups=())`, behind `identity.IdentityProvider`. What each trusts of its provider
  is in cairn/identity.py. Every outbound call goes through `SignIn.http`; OIDC discovery is
  fetched when the provider is first used. A provider's answer that is not what cairn can use
  is a failed sign-in (502), never a crash.
- GET /login lists one link per provider (/auth/<name>, carrying `next` when it is not "/")
  and no key form; POST /login is 404. GET /login?error=cancelled shows a fixed message;
  nothing from the URL is shown.
- GET /auth/<name>[?next=/path] redirects (302) to the provider with `client_id`,
  `redirect_uri` = the issuer + "/auth/<name>/callback", `response_type=code`, a scope, a
  random `state` and, for OIDC, a random `nonce`. The state, nonce and `next` travel in the
  sign-in state cookie (`__Host-cairn_signin` at Path=/ on https, `cairn_signin` at
  Path=/auth over plain http; `signin` under a brand; HttpOnly, SameSite=Lax, 10 minutes),
  sealed with a secret made at start-up and bound to that provider. Several sign-ins can be
  under way in one browser (B2): a start adds its state to the cookie and replaces none, the
  cookie holds at most five, each sealed alone and each for its own 10 minutes, and a start
  drops the oldest past five or past 3,800 characters (cairn/web/signin.py). A `next` over 1024
  characters (an agent's consent page with a long `state` or redirect URI) is held whole in
  the process's memory behind a short random handle that the cookie carries instead, so the
  cookie stays under the browsers' 4 KB limit: at most 4096 are held (the oldest dropped
  first), each at most 8 KiB (a longer one keeps only its path), each dropped once used or
  after ten minutes; an unknown or expired handle gives "/". Nothing is written.
- GET /auth/<name>/callback finds its `state` among the cookie's before anything else (400
  when the cookie is missing or holds no unexpired state sealed for this provider with that
  `state`, or it was already used, whether or not the browser is signed in; then no call to
  the provider is made), and settles it once. `?error=` then
  goes back to /login?error=cancelled (303). Otherwise the provider turns the code into a
  verified profile, or refuses: 400 (an ID token that is not this sign-in's, or an ID token
  and userinfo that name different emails), 403 (an email not verified by the same answer
  that carries it, a group not allowed, or an email neither in `allowed_email_domains` nor
  in `allowed_emails` when either is set; for Google a domain admits only when the `hd`
  organisation is in them too), 502 (the provider failed).
  An identity is recorded with its provider's issuer (GitHub's and Google's are fixed, an
  OIDC provider's is its `issuer`); a sign-in whose (provider, subject) is known under
  another issuer (the provider was replaced under the same name) is refused with 403 and a
  message that an operator must act. With `join="default"`, a person removed from the
  workspace who has no open invitation is refused with 403 and a sentence telling them to
  ask an owner for one (`Store.sign_in` raises Removed). A refusal writes nothing and sets
  no session. Success
  signs the person in (`Store.sign_in`, with `join`), sets the session cookie as POST /login
  does, takes its own state out of the state cookie (clearing the cookie when no other
  sign-in is under way; a refusal or a cancel does the same), and answers 200 with a
  `<meta http-equiv="refresh">` to
  / when the sign-in took an open invitation to let a removed person back in
  (`SignedIn.invited`: its /join link is used up, and `next` may be that link), else to
  /welcome for a new user (carrying `next` along when it is not "/"), else to `next`: the
  next request is then a same-site navigation that carries the SameSite=Strict cookie. This
  callback is the one GET that writes.
- With `join="personal"`, GET / without a session is a public landing page (P1,
  landing.html) that links to /login, shows the connect commands with this server's own
  /mcp address and names the providers; with
  "default" it redirects to /login, as without sign-in.
- The provider's client secret and the tokens it issues are never stored, logged or shown.

A wall, a brand (B1, frozen by tests/acceptance/test_b1_wall.py; cairn/web/auth.py,
cairn/web/brand.py, cairn/web/signin.py)
- Settings: `serve --wall`, `--brand-name NAME`, `--brand-logo FILE`, `--brand-logo-alt
  TEXT`, `--session-days N`; create_app's `wall`, `brand_name`, `brand_logo` (a path, or a
  brand.Logo read already), `brand_logo_alt`, `session_days`. Left out, nothing changes:
  every byte is today's. Each needs `--sign-in`; `--brand-logo` needs `--brand-name`;
  `--brand-logo-alt` needs `--brand-logo`. A name is 1 to 60 characters with no control
  character and no < or >; a logo is a PNG or an SVG of at most 256 KB, told by its content
  (brand.py says how, and that reading it fetches nothing); `--session-days` is 1 to 365.
  A value that cannot be used, or a setting that would do nothing, exits 2 before the
  database is opened, and the last line on stderr names the option and its rule.
- The wall (Auth): a browser with no web session gets the sign-in page and nothing else.
  What machines use answers as without it: /api, /mcp, /healthz, the OAuth endpoints and
  the /.well-known/ addresses a route serves. The sign-in addresses
  (/login, /auth/...) and /static/ pass. Any other address, known or not: GET and HEAD 303
  to /login (with `next` only for /oauth/consent, /new and /join/<token>, or the come-again
  page when another site sent the browser there), any other method 401 "Sign in first." in
  plain text, except the note form posted as a form (/notes, /b/<project>/notes), which
  gets its 401 as the same form holding what was typed (R1.7), and an error on a sign-in
  address or a file is one line of plain text. Every
  answer to a request that was not admitted, on an address that is not a machine's, carries
  `X-Robots-Tag: noindex`. A signed-in browser is answered as without the wall; an API key
  or an agent's token opens /api and /mcp, never a page.
- The brand: with a brand name, wall or not, nothing a visitor can get names cairn: no
  landing page and no guide for a visitor (303 to /login); the sign-in
  page, the page after it and the come-again page are templates of their own
  (brand_base.html: the name as the whole title and the one heading, the logo on a light
  plate, one link per provider, no navigation, footer, form or script); the error pages and
  the signed-in frame name the brand (base.html: the title's suffix, the wordmark with the
  logo, the icon, the footer); the stylesheet is served as /static/site.css?v=<digest>, the
  product's own with no comment that names cairn plus the plate's rules, and the rest of
  /static/ serves only the fonts and their licence texts, by their exact names
  (app.branded_static: cairn.css, cairn.svg and manifest.webmanifest, by any spelling, and
  anything else there are 404; no manifest is linked);
  the logo is served at /static/logo-<digest>.<svg|png>, its bytes unchanged, with its
  media type, nosniff, a year's immutable caching and, for an SVG and a PNG alike, a policy
  of `default-src 'none'`, a sandbox and `frame-ancestors 'none'`. On https the cookies are
  `__Host-session` and `__Host-signin` (Path=/ both: no other host under the same parent
  domain can set one, and only the name set is read; auth.branded_cookie), over plain http
  `session` and `signin`;
  the OAuth scope is said as `memory` (oauth.BRANDED_SCOPE: both names are one scope, taken
  either way by every server), the protected resource's name is the brand's, and a refusal
  names "this server". What signed-in pages, the guides and the MCP server say under a brand
  is B3's (cairn.web.brand, cairn.web.public, cairn.web.mcp: they say the brand's name, and
  /docs/run is not shown); the token prefixes stay the software's.
- A remembered sign-in (`--session-days N`, auth.Sessions): a web session made by signing
  in, by switching workspace or by taking an invitation lasts N days, the stored session and
  the cookie's Max-Age alike. A use more than a day after the session was made or last
  renewed extends it to N days from then, in one guarded UPDATE (Store.renew_session), and
  sends the cookie again with the same attributes; a session that ended or ran out is never
  revived. Without it: 12 hours, never renewed. Key sessions, API keys and grants are as
  before. When `serve` starts, every person's web session not opened with a key is made to
  end at most the length in force from then (Store.cap_web_sessions), so lowering or
  removing the option shortens the sign-ins already made; nothing is ever lengthened.
- `session-end --db PATH --email ADDRESS` ends every web session of every person whose
  address is that one (the whole address, any case, as the allowlist matches it), and every
  one made by a sign-in the provider vouched for with that address (the allowlist checks the
  address it vouches for now, which may differ from the one a person first signed in with),
  with the sessions switching workspace and taking an invitation made from it, in every
  workspace (Store.end_person_sessions), prints how many and no address, and exits 0, also
  when nobody has it. Each workspace where a session ended gets a `sessions.ended` audit
  event with the member's id and the count. API keys and agents' grants are not touched.

MCP (mcp>=2.3 `MCPServer`, streamable HTTP at /mcp, stateless_http=True, json_response=True)
- The session manager runs inside the parent app's lifespan. DNS-rebinding protection stays
  on: loopback hosts plus `allowed_hosts` are accepted, anything else gets 421.
- `initialize` returns instructions telling agents when to `recall` and when to `note`, and
  to follow each project's guidance.
- Tools (names are cairn's own; project = the id or name of one of the caller's projects):
  projects() -> {"projects": [{"id", "name", "description", "pages", "kind", "guidance"}],
      "kinds": [{"id", "title", "summary"}]}: each project's kind (cairn.kinds; a kind no
      longer shipped reads as "general") and its guidance, then every kind, in KINDS order.
  new_project(name, description="", kind="general", private=None) -> {"id", "name", "kind",
      "guidance"}
      An unknown kind is a tool error naming every kind; a connection that may not create
      projects is refused first, whatever the kind. `private` true: only its maker (the
      caller's person), the people its rule names, the workspace's owners and credentials
      that act for no person reach it; false: every member reads and writes it; left out:
      the kind decides (cairn.kinds.Kind.private: a personal project is private, any other
      open). A name a project of the workspace already has, reached or not, is refused alike.
  note(project, text, title=None, topic=None, label=None, source_ref=None)
      -> {"note_id", "created", "page", "status", "version", "violations"}
      Stores the note and compiles it before returning: a note is searchable at once.
  recall(query, project=None, limit=8) -> {"hits": [{"project", "page", "title", "claim_id",
      "text", "passage_ids", "version", "score"}], "searched": {"complete", "words_searched",
      "words_left_out", "words_left_out_count", "claims_matched", "claims_ranked", "said"}};
      `version` (E2, frozen by tests/acceptance/test_e2_a_hit_names_its_version.py) is the
      version of the page the hit's claim was read on, its latest in the statement that
      read the hit (cairn.store.ranking._CANDIDATE: page_heads, by primary key);
      `evidence` with the hit's project, page, claim_id and version answers with the hit's
      passage_ids;
      `claims_matched_exact`, false, joins them only when a word's count stopped at its
      bound (R6, cairn.store.ranking.COUNTED_MAX, 8,000): more than claims_matched match.
      project=None searches every project of the account; `limit` is 1 to 50, and any other
      is a tool error that says so (Q3). `searched` (Q2, cairn.memory.searched)
      says what the search left out, always: the words after the first 32 (fewer past 64
      parts), and the matching claims not ranked when more match than the search ranks
      (Q3: what a fixed amount of work allows for the parts of its words, 8,000 for one
      part down to 1,000 from eight on, as before; cairn.store.ranking.scored_max).
  page(project, slug, version=None) -> {"project", "slug", "title", "version", "versions",
      "body", "claims": [{"id", "text", "passage_ids"}], "metrics"}
      ("body_not_shown", true, only when "body" is the sentence cairn.compiler.BODY_NOT_SHOWN)
  evidence(project, slug, claim_id, version=None) -> {"claim": {...}, "version", "passages":
      [{"id", "note_id", "note_title", "heading_path", "start_line", "end_line", "text"}]}
      (E1, frozen by tests/acceptance/test_e1_evidence_of_a_version.py: the claim as that
      version of the page holds it, with the passages that version cites; None: the latest)
  overview(project) -> {"project", "kind", "guidance", "notes", "pages": [{"slug", "title",
      "version", "claims", "updated_at"}]}
  related(project, slug, limit=8) -> {"project", "page", "title", "version", "related":
      [{"page", "title", "version", "why": [{"kind": "shares", "term", "term_kind", "pages",
      "here", "there"} or {"kind": "mentioned_by", "term", "term_kind": "title", "there"}],
      "more"}], "searched": {"complete", "terms_searched", "terms_left_out", "terms_cut",
      "title_cut", "claims_read", "pages_left_out"}}
      (KG1; cairn.memory, Related pages; the rules are cairn.relations and the read
      cairn.store.relating.) The pages of the project most related to the page, best first:
      one that states a term the page states (a url, a path, a flag, a version, or inline
      code that is not a plain word; none that more than 5 pages hold), or whose claim holds
      the page's title. `here` and `there` are claims as a hit of `recall` carries one:
      "claim_id", "text", "passage_ids", "outside", "written_by", "written_at". `why` holds 3
      reasons at most and `more` counts the rest; `limit` is 1 to 20, and any other is a
      tool error that says so. Worked out by rule when asked, from the pages as they are, in
      one read: nothing of a relation is stored, no model is asked, and a forgotten note is
      out of it with the write that forgets it. Not in this version: a relation across
      projects, the pages a page mentions, paths of several steps, groups over a whole
      project, anything inferred from meaning. `searched` says what the read's bounds left
      out, always. A read of the page for the audit log (read.page), which never holds a term.
  Unknown projects, pages or claims are tool errors (isError true), never empty successes.
  A note the note guard refuses is a tool error naming each rule and line.
- X1 (frozen by tests/acceptance/test_x1_unknown_fields.py): a tool takes only the arguments
  above. Each input schema says so ("additionalProperties": false), and a call with another
  argument is a tool error before the tool runs, ending "unknown argument 'sorce_ref'; note
  takes 'project', 'text', 'title', 'topic', 'label' and 'source_ref'" (the unknown names
  shown as over REST, below, then every argument in the schema's order; "projects takes no
  arguments"), recorded as a call whose arguments fail the schema is (its caller,
  "invalid", None, 400).

The note guard and the logs (frozen by tests/acceptance/test_g1_guard.py; cairn/guard.py)
- Every note, whatever carries it, is screened before anything of it is stored, by
  `Store.add_note` (see cairn.store): its text, title, topic, label, source_ref and
  via. The guard is `create_app(..., guard=...)` when given (any `cairn.guard.Guard`,
  put on each request's view with `Store.with_guard`), else a `PatternGuard` with the
  workspace's "guard.policy" setting ("secrets", the default; "secrets+personal"; "off"),
  which applies to narrowed keys too. Unknown project: 404 first. A refusal stores nothing of the
  note: REST answers 422 {"error", "findings": [{"rule", "kind", "line"}, plus "field" for
  a field other than the text]}, MCP a tool error with the same rules and lines, the
  composer 422 with "Nothing was saved. Remove what is listed below and save again.", each
  rule and its line or field listed above the form, and the text and every field still in
  the form as typed (escaped) for that session alone to fix. No answer to an API or MCP
  caller, log or audit event holds what matched. It is recorded as `note.refused` with
  the rules; a caller that may not write gets 403 whatever its note holds, and nothing is
  recorded.
- The audit log (Store.list_audit): the workspace's writes and security events, and with
  `serve --audit-reads` its reads, newest first; each workspace keeps its newest 100,000
  (store.AUDIT_KEEP) unless `serve --audit-keep N` or `all` says otherwise (Store.audit_keep;
  older ones are deleted, as the page says), and each stored event is also one JSON line on
  the logger "cairn.audit" once its write commits (on stderr; one committed just before the
  process dies has none). GET /audit ("Audit", listed on /workspace) shows 200 events at a
  time (`before` gives older ones; `action`, `project` and `who` filter; CSV and JSON
  downloads at /audit/download) to whoever `may_manage_credentials` admits; a member gets
  403. An admin who does not reach every
  project sees none of the events of a project out of their reach, and no such project's id in
  an event's reach (Store.list_audit), nor workspace.imported, which counts the whole
  workspace's content; an admin who reaches every project still sees no event of a removed
  source (also once a source of that name is added again) or of a project that no longer
  exists; owners see everything. A caller is recorded as ("key",
  label), ("token", client name), ("session", person's name) or, for a session opened
  with a key of the workspace's own (made without a person), ("session", key label); a
  person's events carry their user id, also those of a key they made or a session opened
  with one, which act for them. Names repeat, so each event also has the actor's id (the
  key's, the grant's or the person's; the key's for a session opened with a workspace
  key), shown on /audit as its last six characters, the whole id on hover.
- The access log: each MCP tool call and REST request is one INFO record on the logger
  "cairn.access" (Memory.access) with `actor_kind`, `actor`, `actor_id`, `action` (the MCP
  tool's name; REST GET /api/search is "recall"), `project` (the name the call resolved, else
  None) and `status` (200, or the RequestError's status, or 500). Never a query, note text, a
  credential or a project the caller named that does not resolve. Reads are recorded there,
  the web view's too, and with `serve --audit-reads` also in the audit log (read.page,
  read.evidence, read.search, read.graph, read.note, read.notes; Memory.access): each GET of
  a page that shows memory is one record, named as the MCP tool that reads the same (/
  "projects", /b/<project> "overview", /b/<project>/p/<slug> "page", /search "recall") or
  after what it asks (/b/<project>/notes "notes", /b/<project>/access "project_access",
  /b/<project>/delete "ask_delete_project", the forget page "ask_forget"). The pages of the
  workspace's settings, people and credentials, and the composer's empty form, are not on
  it; what the web view writes is in the audit log. A request to /api or /mcp refused
  before its route (no valid credential) is one record ("none", None, None, "denied",
  None, 401), never with what it presented; an MCP call whose arguments fail the tool's
  schema is one record (its caller, "invalid", None, 400), never with the arguments. A log
  handler that raises fails no request.

The guard's policy on the workspace page (frozen by tests/acceptance/test_g1b_policy.py;
cairn/web/workspace.py)
- GET /workspace has a section with the id "guard" for everyone in the workspace: what the
  guard refuses now, in words (cairn.guard.policy_words).
- For an owner or an admin the section holds a form posting to /workspace/guard with one
  choice named `policy` per value of `cairn.store.GUARD_POLICIES`, each with words a person
  can read, the workspace's current one chosen. A member sees the words and no form.
- POST /workspace/guard with one of those values changes the setting, recorded as one
  setting.changed event under the person, and redirects to /workspace. Choosing the value
  it already has changes and records nothing. A member gets 403. Another value, or none,
  is 400. Nothing changes on a refusal.
- The choice holds at once, for every door a note comes through.
- When the server was given a guard of its own (`create_app(guard=...)`), a workspace's
  choice would do nothing: the section says the operator of the server decides, there is
  no form, and the POST answers 409.

REST (JSON; project = id or name; unknown project or page, or another account's -> 404)
- X1 (frozen by tests/acceptance/test_x1_unknown_fields.py): every address under /api takes
  only the body keys and query parameters listed below; /api/account/export takes none.
  Another is refused before the address does anything else, so nothing changes: 400 and one
  sentence, "unknown key 'sorce_ref'; this address takes 'text', 'title', 'topic', 'label'
  and 'source_ref'" ("unknown query parameter ..."; "this address takes no query
  parameters"), then every name the address takes in its own order. The unknown names are
  sorted and shown through repr (no line break or control character is written raw), the
  first 5 of them, then "and N more"; one longer than 64 characters as repr writes it is
  cut there and followed by "..." after its quotes ('zzz...zzz'...), so a refusal stays
  short whatever is sent (cairn.memory.not_taken). The handlers read a body and a query
  only through cairn.web.rest's `_body` and `_query`. Web forms, the OAuth addresses and a
  connector's items still ignore what they do not know.
- GET /api/projects (the `projects` tool's answer); POST /api/projects {"name", "description",
  "kind", "private"} -> 201 with `new_project`'s answer (400 invalid name, or an unknown or
  empty kind with every kind named, as over MCP; "kind" absent or null is general;
  "private" absent lets the kind decide, as over MCP, anything but true or false is 400;
  409 taken, also by a project the caller does not reach, with nothing more said of it)
- A taken project name gets one sentence everywhere (web form, REST, MCP, guided setup):
  "The project name '<name>' is not available: it already exists in this workspace."
  (cairn.memory.name_unavailable; the setup adds which use needs another name), the same
  whether the project holding it is one the caller sees or not. Names are unique per
  workspace, so this is the one thing a hidden project gives away: that its name is taken.
- POST /api/projects/{project}/notes {"text", "title", "topic", "label", "source_ref"} -> 201
  with the same fields as the `note` tool; 422 with findings when the guard refuses it;
  400 naming the field when one is longer than cairn.store.FIELD_MAX allows (title and
  topic 200 characters, label 80, source_ref 500, via 40), and 400 naming "headings"
  when a passage sits under headings past cairn.store.HEADINGS_MAX (400 UTF-8 bytes in
  all) or the note's headings outnumber its passages by more than HEADINGS_OVER_PASSAGES
  (8) (cairn.memory.LongHeadings, quoting nothing of the note), as the `note` tool's error
  and the composer's form again (400) also say; a source's draft like that is skipped
- GET /api/projects/{project}/pages; GET /api/projects/{project}/pages/{slug}[?version=N]
- GET /api/projects/{project}/pages/{slug}/claims/{claim_id}/evidence[?version=N] (E1)
- GET /api/projects/{project}/pages/{slug}/related[?limit=N] (KG1): what `related` answers;
  N is 1 to 20 (8 when left out), and any other is a 400 that says so; a page that is not
  there is the 404 `page` gives, with the nearest page names
- GET /api/search?q=...[&project=...][&limit=N]: what `recall` answers, `searched` included;
  N is 1 to 50 (8 when left out), and any other is a 400 that says so (Q3)
- GET /api/usage: the workspace's plan and what it has used of each cap (Plans, below)
- GET /api/audit[?before=N][&limit=N][&action=...][&project=...]: the audit log, newest
  first, as /audit reads it (Store.list_audit), for a caller that manages the workspace (403
  otherwise, a narrowed key included): {"events", "events_total"} and, while older events
  match, `events_next`, to pass as `before`; `limit` is 1 to 1,000 (100 when left out)
- GET /api/graph?project=...[&limit=N]: the project's graph (cairn.graph.project_graph), its
  pages, their claims, the passages those cite and their notes; N is 1 to 20 pages (20 when
  left out). A key only; a project out of reach is a 404 as for one that does not exist; the
  web page is GET /b/<project>/graph (cairn.web.graph_routes), and an agent reads the same
  projection at the MCP resource graph://project/<project>/<limit> (cairn.web.graph_resource)
- DELETE /api/projects/{project}?confirm=<its name> -> {"deleted": true} (H5b;
  Store.delete_project); DELETE /api/projects/{project}/notes/{note_id} -> {"note_id",
  "rebuilt", "removed"} (page slugs; Store.forget_note). 404 for a project, or a note of it,
  out of reach, and for a note of another project; 403 for a caller who reaches the project
  but did not make it and is not an owner, or a key limited to some projects or read-only;
  400 when `confirm` is not the
  project's name exactly. On the access log under actions "delete_project" and "forget", with
  no id of what went (that is in the audit log). A note whose text was forgotten in its
  project is refused everywhere a note is written: 409 over REST
  (cairn.memory.NoteForgotten, naming no text), an error from the `note` tool, the
  composer's form again with what was typed (409), and a source's draft is skipped with
  reason "forgotten" and its cursor moves on. An import never writes into a project that has
  forgotten anything, since it makes new projects under free names.
- A page holds at most cairn.store.PAGE_CLAIMS_MAX (2,000) claims, on every install, with
  or without plans (H6c). A note whose passages would take the page it compiles into
  (the page of its topic key, Store.topic_page; N1, below) past that is refused before it is
  stored: 409 over REST (cairn.memory.PageFull), naming the project and the page and saying
  to give the note another topic (a note alone past the bound: to split it), never its
  text; an error from the `note` tool; the composer's form again with what was typed
  (409); a source's draft is skipped with reason "page full" and its cursor moves on. A
  note the project holds already is "unchanged", never refused for a full page. When another
  note filled the page meanwhile the note is kept, the page is not changed, and the answer
  is the same 409 saying so.
- Notes are filed by topic (N1, frozen by tests/acceptance/test_n1_note_filing.py;
  cairn.topics, cairn.compiler.page_for, cairn.web.filing). A note goes on the page of its
  topic key, made once from its topic, else its title, when it is stored; a key no page has
  makes a new page. The same text and source_ref sent again for another topic is refused
  and nothing is stored: 409 over REST (cairn.memory.FiledOnce, naming the project and the
  page when that page cites the note, else saying it waits or is on no page; never the
  text), an error from the `note` tool, the composer's form again (409), and
  a source's draft is skipped with reason "filed under another topic". A note whose page
  moved under it more often than the compiler retries, twice over, is stored and waiting:
  202 over REST with status "waiting" and a sentence, a result (not an error) from the
  `note` tool, the composer's form cleared with that sentence (202). `serve` files the
  waiting notes itself (create_app's `file_every`), and `python -m cairn.server waiting`
  lists them without their text.

Plans (frozen by tests/acceptance/test_h6_quotas.py; cairn/plans.py, cairn.store's limits)
- Only with `create_app(plans=...)` (`serve --plans FILE`): each request's view is limited
  by its workspace's plan in force (`Store.plan_in_force`: the one the operator set, else
  the default), read from the store at each request in one read (`plan-set` takes effect
  on the next one), as the last layer, after
  for_account, the guard and narrow. Which plans exist, their names, labels and caps are
  the operator's file (deploy/plans.example.yaml); the code holds none. Without plans
  nothing is capped, rate limited or refused for usage.
- A write past a cap stores nothing, is not audited (one access record, status 402: over
  REST and MCP as any call, and from a web form too, as "note" with its project, "new_project"
  or "start"; a web form that writes adds none) and answers, on /api, 402 with exactly
  {"error", "limit", "used", "cap", "resets_at", "plan"}:
  `error` is one plain sentence with "<used> of <cap>", the plan's label, what the plan
  allows in its words (cairn.plans.limit_sentence: "the Free plan allows 3 notes a month")
  and, for the month's notes, the UTC day it resets ("1 December 2026"); `resets_at` is
  that day's ISO timestamp, null for storage, passages and projects. `limit` "passages":
  the note (or archive) would take the workspace past the passages its storage cap
  allows, one per cairn.store.BYTES_PER_PASSAGE bytes of it. A connection that may not read
  /api/usage (Memory.sees_totals: narrowed to some projects, read-only, or acting for an
  admin or a member) learns no more from a 402: exactly {"error", "limit"}, the sentence
  without a number or the plan; the composer's answer to them holds neither either. Over
  MCP the same sentence is the tool error. In the UI the composer, the /projects form and
  /start answer 402 with the sentence (role="alert") and what was typed. A duplicate note
  is "unchanged", never refused. A source's draft past a cap is not declined: it and every
  draft after it wait for the next run (the report's `capped`), and the cursor stays
  before it.
- A plan's `requests_per_minute`: a workspace's requests (key, grant or session; after
  sign-in, before any route) past it in a sliding minute get 429 with Retry-After (whole
  seconds); on /api and /mcp the JSON {"error"} and one access record with action
  "rate_limited" and no project, in the UI the error page. The count is in this process's
  memory (cairn.web.ratelimit), per workspace, bounded, and never written. The window is
  counted by whole second (a request at 0.9 s leaves it at 60 s). When the bound is reached
  a workspace whose window is empty is forgotten first, and one with a live count only when
  every tracked one has one; a new workspace is never refused for want of room.
- GET /api/usage and GET /usage ("Usage" on /workspace) show the workspace's totals,
  which count every project, so only to whoever reaches every project for the workspace
  (H6c, cairn.memory.Memory.sees_totals): an owner, a key an owner made, or a credential
  that acts for no person. 403 for an admin or a member (and their keys and agents), and
  for a key narrowed to projects or read-only; /workspace does not list Usage for them; 401
  without a credential. Both answer {"plan", "label", "period", "resets_at", "limits"},
  `limits` mapping notes_per_month, storage_bytes, projects and model_budget_micros to
  {"used", "cap"} (cap null: none), and beside it `passages`, {"used", "cap"}: the
  passages (paragraphs, list items, quotes, code blocks, tables) the notes hold, and the
  storage cap's passages (null: none). /usage also lists what the plan allows in its words
  (cairn.plans.limit_sentences) and says each amount as people do ("768 bytes of 500 MB",
  "$0.00, no cap": cairn.plans.quantity). Reading either writes nothing. There is no usage tool:
  the MCP tool set is frozen at seven.
- A plan's `versions_per_page`: each save keeps the page's last N versions and drops the
  older ones; asking for a dropped version (?version=N, on /api or /b) is 404 with a
  sentence that says it is no longer kept and what the plan keeps. A plan's storage cap
  bounds the history too: past cairn.store.HISTORY_FACTOR times it, a save drops the
  workspace's oldest versions, never a page's latest (cairn.store, Limits); a dropped
  version is the same 404.

Web UI (server-rendered HTML)
- Navigation (P1, frozen by tests/acceptance/test_p1_pages.py; templates/base.html): a
  signed-in page links Projects (/), Search (/search), New note (/new) and Workspace
  (/workspace), then the person's name linking to /workspaces (none for a session acting
  for no person), then Sign out, a form. A visitor's page links Guides (/docs) and Sign in
  (/login).
- GET /workspace (cairn/web/workspace.py): the workspace's name as the heading, then a
  list (`ul#parts`) of Agents, Keys, Sets, Members, Sources, Usage, Audit log, Setup
  (/start/connect) and Data and deletion (/account), each a
  link and one sentence. A part is listed exactly when its page opens for the caller:
  each part's `opens` is the function its page asks.
- Every page links the stylesheet as /static/cairn.css?v=<the first 12 hex digits of its
  SHA-256>, computed once at start (templating.STYLESHEET), so an upgrade changes the
  address and no browser keeps an old copy; no page links the bare address.
- Every listing table gives each body cell a `data-label` naming its column, and under
  600px each row reads as a card. A claim's Markdown tables get the same labels.
- GET /docs lists the guides; GET /docs/<name> renders one of connect, patterns,
  sources, security, privacy and run from the Markdown shipped in cairn/guides (headings and
  anchors, raw HTML off, a list of sections at the top); any other name is 404. The
  landing page's "On your own machine" leads to /docs/run; no served page links to the
  source repository.
- GET / lists the account's projects, each with its kind; with no project yet it points to
  /start. GET /b/{project} shows the project's kind with what belongs in it and what to keep
  out, lists pages and has a search box; GET /search?q=...[&project=...] shows hits linking
  to pages, and above them `searched`'s sentence: what the search left out, or that it left
  out nothing (Q2).
- GET /b/{project}/p/{slug}[?version=N]: every claim is an element with id="<claim id>" that
  links (href ending in "#<span id>") to each cited span; every cited span is shown as an
  element with id="<span id>" holding its source text, note title and line range. The
  page's gate metrics are shown under "Checks" in words (web/ui.CHECKS; a name without
  words as it is), each row carrying its name (data-metric), and each source says which
  claims cite it by their number on the page. Earlier versions are linked.
- GET /setup: how agents connect: with only the URL when OAuth is on (the MCP URL, and the
  `claude mcp add --transport http cairn <url>` and `codex mcp add cairn --url <url>`
  commands; else how to turn it on), and with an API key: the MCP URL (public_url + "/mcp",
  or a placeholder), then without sign-in an env file only its owner reads (`cairn.env`,
  mode 0600) and a link to the connect guide for each client's steps, with sign-in a pointer
  to /keys instead. It shows no key values.
- GET /welcome: where a new person lands: the same one-URL connect as /setup, and a link on
  to `next` (a path on this server) when there is one. While the workspace has no project it
  leads to /start first.
- The guided setup (U1; cairn/web/start.py has the details). GET /start asks what cairn
  will be used for: a checkbox `use` per kind except "general" and a name field
  `name-<kind>` holding its name hint. POST /start makes one project per ticked use, of that
  kind, and answers 303 to /start/connect; every use and name is checked before any project
  is made (400 for nothing ticked, a use not offered, an invalid name or one name for two
  uses; 409 for a name taken by a project of another kind or one the caller does not reach,
  in the one taken-name sentence; each with the form again, a
  message (role="alert") and what was chosen), and a project that already has that name and
  kind is kept. GET /start/connect shows, for each project, how what uses it connects: the
  MCP URL and the `claude mcp add` and `codex mcp add` commands for kinds that connect
  agents (with OAuth off, the way to a key), and for kinds that connect an app a form
  posting to /keys (`label`, `scope` "selected", `projects` the project's id, `access` "write")
  for a key that reaches only that project; a member who may not make keys is told who can.
  Neither GET writes. The setup makes projects; it never renames or deletes one.
- GET /agents: "Connected agents", listed on /workspace (see OAuth).
- GET /keys lists the workspace's keys by label (never a key), each with its reach and
  access as on /agents. Under "New key", POST /keys with `label` (1 to 80 printable
  characters after stripping; 400 otherwise) and the consent page's `scope`, `projects` and
  `access` (the same choices, defaults and 400s) makes a key and shows it on that response
  only, alone and then in the two lines an app needs in its environment (CAIRN_URL, the
  public URL or a placeholder, and CAIRN_API_KEY). POST /keys/<label>/revoke revokes every
  key of the workspace with that label: 303 to /keys, 404 when it has none. In both modes.
  Making and revoking keys is for whoever `web.auth.may_manage_credentials` admits: a
  caller acting for no person (a key of the workspace's own, made without a person, or a
  session opened with one) or a person whose role in the workspace is owner or admin (a
  key a person made, and a session opened with it, act for that person as they are now).
  A member gets 403 with the reason on both POSTs, and
  GET /keys shows them the labels without the form or the revoke buttons.
- GET /sets ("Sets", listed on /workspace) lists the workspace's sets with their
  projects by name. POST /sets (`name`, `projects`: project ids) makes one: 303 to /sets, 409
  when the name is taken, 400 for a bad name or an id that is not a project of the workspace.
  POST /sets/<id> (`projects`) replaces its projects (none ticked: an empty set) and
  POST /sets/<id>/delete removes it: 303 to /sets, 404 for an unknown set or another
  workspace's. Managing sets is a credential-level action: the same
  `may_manage_credentials` rule, the same 403, and GET /sets shows a member the sets
  without the forms. A key or agent whose reach names a deleted set no longer reaches its
  projects.
- `may_manage_credentials` asks the store (`Store.manages`): a caller acting for no person
  that reaches the whole workspace, or a person who is now an owner or admin of it. Every
  call it guards also refuses on its own in the store (PermissionError), so the pages only
  decide what to show.

People and who reaches a project (T1; cairn/web/members.py)
- A credential a person made acts for that person: a web session, an agent they approved
  and an API key they made reach what the person reaches at the moment of the request
  (owners: every project; anyone else: what each project's rule gives them, see cairn.store),
  narrowed further by the connection's own reach, and nothing once they have left. A project
  out of a person's reach is answered exactly like an unknown one, everywhere above.
- GET /members ("Members", listed on /workspace): the people and their roles, for
  everyone in the workspace. A manager also gets the invite form (`email`, `role`), per
  person a role form and a remove button, and the open invitations with a revoke button.
  POST /members/invite answers 200 with the link to send, `<public_url, else the request's
  base URL>/join/<token>`, shown on that response only (400 with the form again for an
  address or role the store refuses). POST /members/<user id>/role (`role`),
  POST /members/<user id>/remove and POST /members/invites/<id>/revoke answer 303 to
  /members; 403 for a caller the store refuses (a member; an admin touching an owner), 404
  for someone or something unknown, 400 for an unknown role or the workspace's last owner.
- GET /join/<token>: signed out, 303 to /login?next=/join/<token>; signed in, the
  workspace's name, the address and role the invitation is for, and a form posting to the
  same path (`Referrer-Policy: no-referrer`); 404 for a dead invitation. It writes
  nothing. POST /join/<token> takes it (`Store.accept_invite`) and answers 303 to / with a
  new session cookie for that workspace; 403 with the reason for a person it is not for,
  or a session acting for no person; 404 for a dead invitation; nothing is written on
  either. Tokens are kept as digests and never logged.
- GET /workspaces lists the person's workspaces with their role in each and writes
  nothing; POST /workspaces/<account id>/switch answers 303 to / with a new session there
  (404 when the person is not a member of it). The session they came with stays.
- GET /b/<project>/access ("Who reaches it", linked from the project's page) shows the rule:
  `everyone` as "write", "read" or "none" radios and, per member who is not an owner or the
  maker, a select named `people` whose values are "<user id>:none|read|write|manage",
  worded "What everyone gets", "Read only", "Read and write" (the Scope component's words,
  memory.ACCESS_LABELS) and "Read and write, and decide who reaches it" (web/members.py
  PERSON_LABELS). It says "Private means only the maker, the people named, and the
  workspace's owners reach it", and that the keys and sources of whoever runs the server
  reach it too; who changes the rule (the owners, the maker and anyone at "manage"); and,
  in one sentence, that deciding who reaches a project is not a workspace role: a workspace
  admin reaches the project only as its rule lets them.
  POST to it replaces `everyone` when given and the people when `people` is given (none
  given: they stay) and answers 303 back to the page. 404 for a project out of reach, 403
  for a person who is neither its maker, nor an owner, nor at "manage" on it (on GET as
  well), 400 for a value the store refuses, a malformed `people` entry or one person given
  twice.
- "Add several people", a form on that page: POST /b/<project>/share with `emails`, a
  textarea of addresses separated by commas, semicolons, spaces or new lines, and `level`,
  "read", "write" or "manage" (`Store.share_project`). Every address a member's: 303 back to
  the access page. Some not: 200, the access page with a notice listing those addresses as
  typed (escaped, each once) and saying they are invited under People first; the members
  among them were given the level all the same, and no one was invited. 400, the access
  page with the reason and what was typed, for an empty or unusable list (more than 200
  addresses, an entry that is not one address) or a level not one of the three; 403 and
  404 as for the access page. Nothing is written on any of these refusals.
- The new-project form on / has a `private` checkbox (the `new_project` tool's `private`):
  ticked is private; unticked lets the kind decide, so a personal project is private either
  way. The guided setup (/start) makes each project with its kind's default too.
  GET /keys names who made each key ("The workspace" for a key made without a person).

Sources (cairn.sources; the store keeps them, see cairn.store)
- GET /sources ("Sources", listed on /workspace) lists the workspace's sources: name,
  kind, the address it reads, the projects it writes into, its last run and that run's counts
  or error; the drafts it left for the next run, for time or for the plan's cap; and, for
  a source held at the plan's monthly cap (cairn.sources), the day it is read again unless
  the plan changes ("Held at the plan's monthly cap"). It never shows a credential (the
  store holds none) and does not list the environment variables a config names, though a
  failed run's error names the one that was missing. It is for whoever
  `may_manage_credentials` admits; a member gets 403, and the store views of a narrowed or
  read-only connection and of a member list none. Reading it writes nothing. No route
  under a path that contains "/sources" accepts anything but GET: a config names the
  server's environment variables, so only the operator adds a source from a config, syncs
  one or registers an account, on the command line (cairn.web.cli); a source made from an
  account is added, switched and removed under /api/integrations with a key (R2,
  cairn.web.rest), naming no variable. The MCP tools do not name sources.
- A source is on or off (S2; frozen by tests/acceptance/test_s2_source_switch.py, the
  rules in cairn.store.sources and cairn.web.ui). /sources shows each one's state in words
  ("On since <when>" or "Off"), where it writes as a Scope, and its switches: owners and
  admins post to /integrations/<name>/on (fields `project`, repeatable, and `new_project`, a
  private project made in the same write) and /integrations/<name>/off. Switching on reads
  only what happens from then on; a source that is off is not read.

Writing from the web view (frozen by tests/acceptance/test_m1_mobile.py; cairn/web/compose.py)
- GET /b/{project} links to GET /b/{project}/new, a composer: one form posting to
  /b/{project}/notes with a `text` textarea and optional `title`, `topic` and `source_ref`.
- POST /b/{project}/notes stores and compiles the note through `Memory.note`, as the `note`
  tool does, with via "web", and redirects 303 to /b/<project name>/p/<slug>; a note that
  compiles into no page (only headings) lands on /b/<project name>. Line ends are stored as LF
  and blank optional fields as absent, so a note typed in a browser is the same note as
  one sent by the tool. Blank text: 400 and the composer again with a message
  (role="alert"). A note the gate rejects: 422, the gate's violations listed, what was typed
  still in the form (escaped); the note is kept, as with the tool. Unknown project, or
  another account's: 404.
- GET /new is the same composer with a `project` select of the caller's projects (by name),
  posting to /notes. It pre-fills `title` and `text` from the query string and appends `url`
  on a line of its own, unless the text already holds it: the share target lands here. With
  no project yet it says so and shows the new-project form instead. POST /notes without a project:
  400 and the form again.
- GET / has a form posting to /projects (`name`, `description`, and `kind`, a select of every
  kind, "general" chosen): 303 to /b/<name> when created; 409 (name taken) or 400 (not a
  valid name, or an unknown kind) with the home page again, a message (role="alert") and
  what was typed and chosen.
- Like every UI form, these refuse cross-site posts (403) and write nothing then; opening a
  composer writes nothing.
- Installable: every page links /static/manifest.webmanifest (public, like the stylesheet):
  name, short_name, start_url "/", display "standalone", the SVG icon, and a share_target
  (GET /new with title, text and url). The UI's Content-Security-Policy has
  `manifest-src 'self'`; there is still no script anywhere but one: with `serve
  --graph-explorer`, the graph page alone loads cairn's own (G2; cairn.web.graph_view).
- The two typefaces ship in /static/fonts/ (public, like the stylesheet; SIL Open Font
  License 1.1, each licence beside its font), and the policy has `font-src 'self'`.
- The search box is on the home page and on every project page. Every page is laid out for
  a 375 px screen: one column, 16 px form text, tap targets of about 44 px.

A workspace's own data (H5; cairn/web/account.py; the format is cairn.store's)
- GET /account ("Account", listed on /workspace; web session): the workspace's name,
  a link to GET /account/export and a form posting `confirm` to /account/delete. The link
  and the form are shown to whoever may use them (below); the default workspace's page says
  it cannot be deleted instead of showing the form.
- GET /account/export (web session) downloads the workspace (Store.export) as
  `application/zip` with `Content-Disposition: attachment; filename="cairn-<account
  id>.zip"` and `Cache-Control: no-store`, for whoever
  `may_manage_credentials(memory, OWNER_ROLES)` admits (a caller acting for no person, or an
  owner: an archive holds every project, and an admin does not reach the private ones, so an
  admin or a member gets 403 and sees no link). GET
  /api/account/export does the same for an API key that reaches the whole workspace with
  write access; a key limited to some projects or read-only, or a session of a person who
  does not own the workspace or a key such a person made, gets 403. Neither writes to the
  database; each is one record on the access log, action "workspace.exported". The zip is
  written to a temporary file (in memory up to 8 MiB, on disk past that) and streamed from
  it with its Content-Length. One export at a time per workspace, in either view: another
  asked while one runs, its download included, is 429 with `Retry-After: 30`.
- POST /account/delete (web session, same-origin): for an owner, or a session opened with a
  workspace key (`may_manage_credentials(memory, OWNER_ROLES)`; anyone else gets 403 and the
  page again with the reason, role="alert"). The default workspace cannot be deleted this
  way: 403, with the reason. `confirm` must equal the workspace's name exactly (no trimming,
  no case folding), else 400 and the page again with the reason. Then the workspace is
  deleted as Store.delete_account deletes it, through the caller's own view, which decides
  inside the write that deletes that the caller is an owner now (or acts for no person): a
  person demoted or removed since the check gets 403 and the page again, and nothing is
  deleted. Else the session (gone with the workspace) is ended, its cookie cleared, and
  the answer is 303 to /. Nothing in the web view compacts the database. The deletion cannot
  be recorded in the audit log it deletes: it is one line on the access log,
  "workspace.deleted <account id> by <actor>".

Deleting a project and forgetting a note (H5b; cairn/web/erasure.py)
- GET /b/{project}/delete says how many notes and pages go, that it cannot be undone, and
  asks for the project's name; POST with `confirm` deletes it (400 and the page again with the
  reason, role="alert", when the name differs) and answers 303 to /. GET
  /b/{project}/notes/{note}/forget says which pages change and which go, that their earlier
  versions go too, that it cannot be undone and that the same text will not be stored in
  this project again; POST forgets it and answers 303 to the project. Both are web sessions,
  same-origin, 404 out of reach and 403 to whoever may not (Memory.erasable); a GET removes
  nothing. The project page links the first and each cited source on a page the second, only
  for whoever may. MCP has no tool for either: the tool set stays at seven.

`create_app(store, gate=None, allowed_hosts=(), public_url=None, sign_in=None, guard=None,
sync_every=None, source_http=cairn.sources.runner.source_client, plans=None, clock=None)`:
`plans` caps every workspace (Plans, above) and the background sync too, by the plan in
force; `clock` is what the rate limit reads (an aware datetime; the store has its own).
With `sync_every`, the
lifespan runs `Accounts.sync_sources` (cairn.sources.sync_forever with the app's gate and
guard and this process's environment) as a background task, which never holds up a request:
notes are written on worker threads. Shutdown cancels it, lets a note being written finish,
then closes its client; the run it stopped releases its source. Under 1 s: ValueError.
guard None means each workspace's PatternGuard policy (above); gate None
means compiler.provenance_gate (`serve` composes extractively; T5). Compiles hold no lock:
two notes on one page, from one process or several on one database, both land
(compiler.compile_note). One process serves an app: the consent secret, the
sign-in secret, the settled requests and states and the registration counts live in its
memory, each bounded (oauth.SETTLED_MAX settled ids per table, forgotten at their deadline
in deadline order; a sign-in callback without a `code` settles nothing).

CLI: `python -m cairn.server serve --db PATH [--host 127.0.0.1] [--port 8765]
[--public-url URL] [--allowed-host HOST]... [--trusted-proxy ADDRESS_OR_NETWORK]...
[--sign-in github|google|oidc]... [--join personal|default] [--allowed-email-domain
DOMAIN]... [--closed-directory] [--registrations-per-hour N]` (an option shown with "..."
takes one value and is given once per value, as often as needed, `--allowed-host a.example
--allowed-host b.example`, and two values after one flag exit 2;
--registrations-per-hour is how many agents one client address may register in an hour, 20
by default, a whole number of at least 1, else exit 2; only a
peer named by --trusted-proxy may say who the client is, through X-Forwarded-For, and the
per-client limits count that address; without it no peer is, loopback included; an entry
that is neither an address nor a network exits 2; cookies' Secure flag follows
--public-url whatever the request's scheme; sign-in needs --public-url, else exit 2; sign-in
providers read their client ids, secrets and OIDC settings from CAIRN_* environment
variables, never from arguments; see cairn/web/cli.py; a missing variable, or a group
variable set but blank, exits 2 naming it; `--sign-in oidc --join default` needs
--allowed-email-domain, CAIRN_OIDC_ALLOWED_GROUPS or --closed-directory, else exits 2 naming
the three; notes pass
gate.lint_gate, the T3 lint core over their cited spans; uvicorn's access log is off,
because it prints query strings, which carry a client's `state` and the sealed request; the
"cairn.access" log goes to stderr instead, one plain line per call, not propagated) and
`python -m cairn.server key-create --db PATH --label L --out FILE` (writes CAIRN_API_KEY=<key>
to FILE with mode 0600 and prints only the file path, never the key) and `python -m
cairn.server key-revoke --db PATH --label L` (revokes every key with that label; 1 for a
missing database, which it does not create, or no key with that label) (A1: it lists, one
per line, the keys and agents made with those keys, at any depth, and says they stay, or
with `--with-made` revokes them in the same transaction; a key made before recording began
gets one more line) and `python -m
cairn.server session-end --db PATH --email ADDRESS` (B1: ends every web session of the
person with that address, see "A wall, a brand"; 1 for a missing database, which it does
not create). serve's B1 options, `--wall`, `--brand-name NAME`, `--brand-logo FILE`,
`--brand-logo-alt TEXT` and `--session-days N`, are in "A wall, a brand". The data commands
(cairn/web/cli_data.py) print paths, counts, the workspace's id, the names of the note fields
an import cut, the notice that imported projects are private, and refusals that name a file,
an id, a project's name, a rule or a limit, and failures that name the file and the system's
reason or the error's class; never note text, keys or tokens. They exit 1 with the reason on
stderr: `export --db PATH --account ID --out FILE` writes the workspace's export to a new
file with mode 0600 (never overwriting one or following a link; 1 for a missing database,
which it does not create, an unknown workspace, or a write that fails, "cannot export to
FILE: <the system's reason or the error's class>", which removes the file); `import --db PATH
--account ID --in FILE [--max-bytes N]` restores an export into that workspace (the
database is created when missing, like key-create's; 1 when the import is refused, nothing
written: a database this run created is removed again, with the directories made for it),
and after the counts prints how many note fields it cut to their bounds (an
export written before cairn.store.FIELD_MAX) and one sentence saying every imported project is
private,
reached only by the workspace's owners (the archive carries no access rules); it reads at
most 256 MiB of the archive, all held in memory, unless --max-bytes raises the bound;
with `--plans FILE` the workspace is held to its plan in force as `serve` holds it (over a
cap: 1, "nothing was imported" and the limit's name; a file it cannot use: 2), and
without it one line on stderr says no plan is applied; `compact --db PATH` runs
Store.compact (1 for a missing database, when its disk has not room for the rewrite, or
when another process kept it from finishing).
`serve --compact-every HOURS` (24 by default; 0: never) runs Store.compact in the
background, on a worker thread and never two at once, when Store.compaction_state says a
workspace was deleted since the last compaction and HOURS have passed since that one began
(at once when there was none), and Store.empty_log alone when a rewrite already covered
every deletion and only the write-ahead log is left (cairn.web.compactor); each run is one
access-log line: "compact done in N ms" when it finished, "compact not finished: another
connection held the file in N ms" when another connection kept it from finishing, or
"compact failed: <the error's class> in N ms"; a rewrite whose log was held, and a run that
only empties the log, say so in parentheses before " in", and a run without room on the
disk says "compact not started: the database's disk has N bytes free, a rewrite needs N".
`serve --sweep-every SECONDS` (600 by default; 0: never, otherwise at least 1; create_app's
`sweep_every`) runs
Store.sweep_expired in the background, at start and then every SECONDS, on a worker thread and
never two at once: it removes the sessions, invitations, OAuth codes, access tokens, rotated
refresh tokens and grants whose time has passed, in every workspace, and the agents that
registered 7 days ago or earlier and never got a grant. A sweep that removed rows is one
access-log line, "sweep removed N expired rows"; a failed one logs the error's type only.
`serve --sync-every SECONDS` (at least 1, else exit 2) syncs every workspace's sources in the
background (create_app's `sync_every`); while `serve` runs, the "httpx" and "httpcore"
loggers are held at WARNING, since httpx's request lines carry query strings and httpcore's
DEBUG trace carries response headers. `source-add --name N --kind K --config JSON`,
`source-list`, `source-sync (--name N | --all)`, `source-remove --name N`, `source-enable
--name N [--project B]...` and `source-disable --name N`, each with
`--db PATH` and `--workspace ACCOUNT` (the default workspace; `--all` is every workspace and
takes none), manage sources as the operator (cairn.web.cli). A source's keys come from the
environment variables its config names, in the environment of the process that syncs.
`google-authorize --client FILE --kind KIND [--kind KIND]... --out FILE [--port PORT]
[--wait SECONDS]` (C8; cairn/web/cli_google.py holds the contract) opens no database and
takes no --db: run on the computer whose browser a person uses, it runs Google's consent flow
for a Desktop app client, listening on 127.0.0.1 only, and writes that person's
authorized_user credential for the Google sources of each --kind to a new file with mode
0600, never overwriting one; it prints no secret.
`serve --operator-notes FILE` (Markdown, 64 KiB at most; a file that cannot be read, is
larger or is not UTF-8 exits 2) is the last section of /docs/privacy, rendered as a claim is
(cairn.web.public.privacy_guide), in place of the list for the operator to fill in.
`serve --plans FILE` (a file that cannot be read or is not a plans file exits 2 and says
why), `plan-set --db PATH --account ID (--plan NAME | --default)` and `usage --db PATH
--account ID [--plans FILE]` (the month's usage as one JSON object, with the passages held
and, given the plans, the cap of the plan in force); a missing database or an unknown
account exits 1, and `usage --plans` with a file it cannot use exits 2. `workspace-list
--db PATH` (H10) prints the ids those two take, one JSON object per workspace with its name,
how many people belong to it and the plan it is held to now, and never an address; a
missing database exits 1. `source-sync
--plans FILE` holds each workspace to its plan as `serve` does (a draft past a cap waits for
the next run, said as "<n> left for the next run at the plan's cap"); without --plans it
caps nothing and says so on one line of stderr.

A model arranges pages (T4b, frozen by tests/acceptance/test_t4b_arrange.py; cairn.arrange
holds the contract): `create_app(..., arranger=Arranger(...))` queues the page of every note
a request (REST, MCP or the web composer) or the app's background source sync saves
(Memory.saved), and runs the worker in the background when the arranger has `every`. A
model answers with claim numbers only, and cairn saves the page's next version from the claims
it already holds, as "page.arranged" in the audit log and `arranged_by` in the version's
metrics (shown on the page in the web view). Without an arranger nothing is queued and
importing cairn.server imports none of cairn.arrange, cairn.llm or cairn.llm_compiler. The
queue is in memory: a restart forgets it. `source-sync` on the command line runs no
arranger, so the notes it writes are not queued. On the command line
(cairn/web/cli_arrange.py): `serve --model-env PREFIX [--model-price IN,OUT]
[--model-deadline SECONDS]` builds the model from <PREFIX>_BASE_URL, <PREFIX>_API_KEY and
<PREFIX>_MODEL, in the format <PREFIX>_PROVIDER names ("openai", the default, or
"anthropic"), with the request fields of the optional <PREFIX>_REQUEST_EXTRA
(cairn.llm.from_env), and runs the worker in the background, a pass every second, beside the
source sync and the compactor (each started and stopped on its own), through a view of each
workspace limited by its plan under `--plans`; IN,OUT is US dollars per million tokens, and
--model-deadline is how long one call may take, retries included (300 by default; a number
above 0, else exit 2). The worker pauses for ten minutes after five answers in a row it
could not use (T4c, frozen by tests/acceptance/test_t4c_pause.py), so a model that cannot do
the job is not asked again for every note. `arrange --db PATH --account ID [--project NAME]
[--plans FILE] (--model-env PREFIX | --tidy) [--model-price IN,OUT] [--model-deadline
SECONDS]` (exactly one of --model-env and --tidy, else exit 2) makes one pass over the
workspace's pages and prints one line per page, "<project>/<slug>:
<status>[, version <a>[ -> <b>]][, tokens <in>/<out>]" (each bracketed part only when it
applies; cairn/web/cli_arrange.py says when), never claim text; `--plans` is required when
any workspace in the database has a plan the operator set (without it: exit 2, "arrange
needs --plans FILE here"), with --tidy too, since a plan also limits what is kept; a pass that
runs into
the pause stops there, says so on stderr and exits 1. A model that is not configured exits 2
naming the variable, never its value; --model-price without --model-env exits 2.

Tidying with no model (T4f, frozen by tests/acceptance/test_t4f_tidy.py; cairn.arrange
holds the contract): `serve` runs the worker without a model in the background, and
`arrange ... --tidy` makes one pass with it. It keeps each heading's claims together and
folds the claims that repeat, cairn's own two steps of an arrangement, and needs no model,
key or budget; a page it saves shows "Arranged by cairn" in the web view and names
cairn.arrange.TIDY in the audit log. On by default in `serve` (decision 76;
cairn.web.cli_arrange.TIDY_BY_DEFAULT): `serve` with no arranging option runs it as `serve
--tidy` does, `serve --no-tidy` runs no worker and imports no arranging code, the parser
refuses --tidy with --no-tidy, and `serve --model-env` runs the model's worker in its place.
`create_app` runs no arranger unless one is passed. A tidied page prints "<project>/<slug>:
arranged, version <a> -> <b>", with no tokens. `serve
--tidy` with --model-env, --model-price or --model-deadline, and `arrange --tidy` with
--model-price or --model-deadline, exit 2 before anything is opened, with one line, "--tidy
uses no model, so it takes no <option>".

A Google account connected in the browser (cairn/web/google_connect.py holds the contract):
`serve --google-connect [--google-connect-for ADDRESS]...` reads the Web client's JSON and
the sealing key from the environment before the database is opened, and offers Connect
Google on the Sources page to the listed signed-in people.

Bounded reads, a bounded history (Q1, frozen by tests/acceptance/test_q1_bounded_reads.py)
- A read costs what it returns. The home page, `projects` and GET /api/projects, `overview` and
  GET /api/projects/{project}/pages, GET /b/{project}, a page (web, `page`, REST), `recall` and
  GET /api/search, and `evidence` and its REST address each run a fixed number of statements
  (12 at most for a key or its web session; a person's web session the same number at any
  size): counts come from COUNT queries that read no body and no note's text, a page's
  sources and a claim's evidence read their notes in one statement (Store.get_notes), and the home
  page reads how far every project is shared in one (Store.projects_sharing).
- Lists come in parts. `overview` and GET /api/projects/{project}/pages give 100 pages at most,
  by slug (cairn.memory.PAGES_AT_ONCE); `limit` (1 to 100) asks fewer and `after` (MCP: an
  argument; REST: the query) the pages after that slug. An answer that leaves pages out
  holds `pages_total` and, while more follow, `pages_next`, the slug to pass as `after`; the
  next part starts after it however many pages were added meanwhile. `notes` counts
  every note. GET /b/{project} shows 200 at a time, with the count of all and a link to the
  next part (`?after=`). A page's `versions` (`page`, GET .../pages/{slug}) is the latest 50
  (cairn.memory.VERSIONS_AT_ONCE), oldest first within the part; `limit` (1 to 50) takes
  fewer of the latest and `before` (a version's number) those numbered below it. A part
  that leaves versions out holds `versions_total` and, while earlier ones remain,
  `versions_next`, the number to pass as `before`. The web page view lists the latest 50
  numbers, says how many there are, and links to the earlier ones (`?before=`). An answer
  that holds everything has none of these fields: under the bounds every answer is as it
  was. A `limit` that is not a whole number of 1 or more, or a `before` that is not a
  whole number, is 400 (an error from the tool).
- A page's history has a bound without plans. `serve` keeps the newest 50 versions of each
  page (`--keep-versions N`, `--keep-versions all`; create_app's `keep_versions`, None by
  default: every version). A save past the bound removes the page's oldest versions in its
  own transaction (Store.bound_history); reading removes none; a page's latest version,
  its address and its citations stay. With `--plans` each plan's versions_per_page rules
  and `--keep-versions` exits 2. `serve` records the bound it runs with in the database
  (Store.record_history_bound), says at start on one line of stderr how many pages hold
  more (removing nothing then), and `compact` removes by the last record alone
  (Store.prune_by_record; cairn.web.cli's docstring). The web page view says, when older
  versions were removed, that it shows the last N.
- Numbers an operator can read. `python -m cairn.server stats --db PATH [--json]` prints
  them and writes nothing (cairn.web.cli_stats): for the install `schema`, `db_bytes`,
  `wal_bytes`, `free_bytes`, `low_disk` (free space under twice the database's size), and
  for the install and each workspace under `workspaces` (by id) `projects`, `notes`, `pages`,
  `page_versions`, `waiting`, `keys`, `agents`, `newest_note` and `sources` (by kind: how
  many are on, how many's last run failed). Counts, sizes and times only. `serve` logs the
  install's totals with `requests` (responses by class since the last line) as one line on
  a logger of its own, at start and every `--stats-every` minutes (create_app's
  `stats_every` and `stats_wait`); under low disk it is a WARNING with a sentence that says
  writes fail and compaction and backup cannot run. /healthz answers as before, and no
  address serves the numbers.
"""

# The code lives in cairn.web, one module per layer; this module is the stable import path
# and the command line's entry point (python -m cairn.server).
from cairn.memory import MAX_HITS, BadRequest, Conflict, Memory, NotFound, RequestError
from cairn.web.app import MAX_BODY, create_app
from cairn.web.auth import (
    LOOPBACK,
    MEMORY,
    PUBLIC_PATHS,
    SESSION_COOKIE,
    SESSION_TTL,
    UNSAFE_METHODS,
    Accounts,
    Auth,
    transport_security,
)
from cairn.web.cli import main
from cairn.web.mcp import INSTRUCTIONS, ProjectRef, Slug, build_mcp
from cairn.web.rest import rest_routes
from cairn.web.signin import STATE_COOKIE, SignIn, signin_routes
from cairn.web.templating import HERE, UI_HEADERS, templates
from cairn.web.ui import UI_HITS, page_view, session_routes, ui_routes

__all__ = [
    "HERE",
    "INSTRUCTIONS",
    "LOOPBACK",
    "MAX_BODY",
    "MAX_HITS",
    "MEMORY",
    "PUBLIC_PATHS",
    "SESSION_COOKIE",
    "SESSION_TTL",
    "STATE_COOKIE",
    "UI_HEADERS",
    "UI_HITS",
    "UNSAFE_METHODS",
    "Accounts",
    "Auth",
    "BadRequest",
    "Conflict",
    "Memory",
    "NotFound",
    "ProjectRef",
    "RequestError",
    "SignIn",
    "Slug",
    "build_mcp",
    "create_app",
    "main",
    "page_view",
    "rest_routes",
    "session_routes",
    "signin_routes",
    "templates",
    "transport_security",
    "ui_routes",
]


if __name__ == "__main__":
    raise SystemExit(main())
