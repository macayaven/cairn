"""SQLite store for accounts, people, projects, project sets, API keys, sessions, OAuth grants,
notes, spans, compiled pages, sources, usage and plans.

Contract (frozen by tests/acceptance/test_w1_store.py and tests/acceptance/test_h1_accounts.py;
the OAuth part by tests/acceptance/test_h3_oauth.py; people by
tests/acceptance/test_h4_signin.py; reach, narrowed views and sets by
tests/acceptance/test_s1_scopes.py; settings, the actor and the audit log by
tests/acceptance/test_g1_guard.py; project kinds by tests/acceptance/test_u1_setup.py;
sources by tests/acceptance/test_c2_langfuse.py; export, import and compaction by
tests/acceptance/test_h5_data.py; members, roles and who reaches which project by
tests/acceptance/test_t1_members.py; limits by tests/acceptance/test_h6_quotas.py; deleting
a project and forgetting a note by tests/acceptance/test_h5b_forget.py; the sweep of what
has run out by tests/acceptance/test_h5c_expiry.py):
- One database file holds every account. `Store(":memory:")` works for tests. The store is
  safe to call from several threads. Writes go through one writer connection guarded by a
  lock; reads run on a pool of at most base.READERS read-only connections to the same file
  (base.Readers), so a read waits for no write, checkpoint or compaction of this process
  (in WAL a reader and the writer do not wait for each other), and a read that finds every
  reader in use waits for one. A database no second connection can open (in memory, or a
  temporary one) reads on the writer under its lock instead. Each call reads in one
  transaction (one snapshot of the file, holding every write that returned before it
  began) or writes in one. `_observe` attaches a trace callback, a progress handler or a
  row factory to every connection of the store, the readers included.
- The clock (H6). `Store(path, clock=None)`: `clock` returns the current time as an aware
  datetime (a naive one is a ValueError when it is read). Every timestamp the store writes
  and every expiry it checks, in every view of it and in the schema upgrade, reads that
  clock (StoreBase._instant); without one, the real time.
- Every project, API key and session belongs to an account. `Store(path)` is bound to the
  default account, id DEFAULT_ACCOUNT_ID ("acct_default"), which always exists; everything
  below holds for it as it did in v0, when it was the only account.
- `create_account(name) -> Account` (id "acct_" + 12 hex chars; the name is stripped, and a
  blank name or one over 80 characters is a ValueError). `get_account(id) -> Account | None`.
  `list_accounts()` is in creation order, the default account first.
- `for_account(id, user_id=None) -> Store` returns a view bound to that account over the
  same database (KeyError for an unknown account); `view.account_id` names it. With
  `user_id` the view acts for that member (KeyError when they are not one): `view.user_id`
  names them, and the sessions and OAuth codes it creates carry them. Only the store, or a
  plain view (no person, no narrowing or other limit), binds an identity: a view that acts
  for a person or is limited in any way returns itself from `for_account` for its own
  account (and `user_id` None or its own) and refuses another account or another person
  (PermissionError), so it can neither shed its person nor take another's. A view is a drop-in for
  Store: every data method works on it, scoped to its account. Closing a view does nothing;
  closing the store that opened the database closes it. The account methods are not scoped
  for the store and a view acting for no person (the server's, the command line's): such a
  view can create, list, open and delete accounts.
- Other workspaces: a view acting for a person, or narrowed to some projects or to reading,
  is refused every operation that is not about its own workspace (PermissionError,
  reach.NOT_ITS_WORKSPACE, before anything is read or written): reach.OTHER_WORKSPACES
  (creating and listing accounts, sign-in, setting a plan,
  OAuth's clients, codes and tokens, the credential lookups across workspaces, compaction).
  reach.OWN_WORKSPACE_ONLY it keeps for its own workspace or person only: `get_account`,
  `delete_account` and `plan_in_force` of its own id, `list_workspaces` and `accept_invite`
  for its own person, and `get_user` for its own person or a member of its workspace.
  `find_invite` looks an invitation up by its secret, for any view.
- Scoping: through a view, another account's project, note, span or page id behaves exactly
  like an id that does not exist (None, [], {} or KeyError, as below for unknown ids).
  Notes, spans and pages belong to their project's account. A view whose account has been
  deleted is a view of nothing: it reads nothing, it has nothing to revoke or end, and a
  write that would add something raises KeyError.
- Keys and sessions belong to the account of the view that created them. `check_key`,
  `revoke_key`, `check_session` and `end_session` only see the view's own account.
  `revoke_key` ends the browser sessions opened with the keys it revokes, in the same
  write, found by the key's id; `create_session` on a view acting as a key that has been
  revoked since raises PermissionError (credentials.KEY_REVOKED), decided inside the write.
  Sessions opened through an identity provider are not touched.
  `create_key(label, projects=None, access="write")` records the key's reach (see Reach).
  `list_keys() -> list[KeyInfo]` (label, created_at, projects, access, maker_id: the person
  whose view made it, else None; oldest first; never a digest). `resolve_key(key)` and
  `resolve_session(token)` look in every account and return a `Principal` (account_id,
  kind "key" or "session", label: the key's label; for a session the label of the key it
  was opened with (one made by a view acting as that key, see Actor), else None, user_id:
  the person a session was made for, or the person who made a key (T1: a key a person made
  acts for them), else None, projects and access: the key's reach; a session's is None and
  "write") or None; an expired session resolves to None.
  B1, the store that opened the database only (PermissionError on a view):
  `renew_session(token, ttl_seconds, after_seconds) -> bool` makes a person's live session
  made by signing in last `ttl_seconds` from now when its last renewal (its expiry less
  `ttl_seconds`: nothing else is stored) was more than `after_seconds` ago, in one guarded
  UPDATE, never for a session that ended, ran out or was opened with a key;
  `end_person_sessions(email) -> dict[account id, count]` ends every web session of each
  person with that address (stripped, any case), and every one made by a sign-in the
  provider vouched for with it (a session's `vouched_email`, which `sign_in` records and a
  session made from another, `create_session(made_from=token)`, carries), in every
  workspace, in one write; no stored address changes.
  `cap_web_sessions(ttl_seconds) -> int` makes every person's web session not opened with a
  key end at most `ttl_seconds` from now (never later than it would have; `serve` calls it
  at start with the length in force), and says how many it shortened.
- `delete_account(id) -> bool` removes the account and everything it owns, its sets, OAuth
  grants, codes and tokens, its sources, its settings and audit log, its usage and plan,
  its rows in the tables billing wrote (subscriptions, billing_messages, checkouts), its
  memberships,
  invitations, project rules and removal markers included (False for an unknown id,
  ValueError for the default account), and nothing of
  any other account. A view acting for a person deletes only its own workspace, and only
  while the person is an owner, decided inside the write (PermissionError otherwise:
  accounts.NOT_YOUR_WORKSPACE, NOT_AN_OWNER, or NOT_A_MEMBER for one removed since).
  A member left with no workspace is removed too, with their
  identities; a member of another workspace stays. No row, no search index and no search
  statistics of the account are left. In the file itself: the
  database zeroes what it deletes (`PRAGMA secure_delete`, set and read back on every
  connection the store opens, which refuses an SQLite that will not; and FTS5's own
  secure-delete, from SQLite 3.42, set on each account's index when it is made and, on an
  index made before, at its next write, never at open), and delete_account ends with a
  truncating checkpoint (`PRAGMA wal_checkpoint(TRUNCATE)`): the text of the workspace's
  notes, its pages' bodies and its search index are gone from the main file, -wal and -shm
  at once, at a cost that grows with the workspace. When another connection keeps the log
  from being truncated, the deletion stands and a warning on the "cairn.store" logger says
  so. What can outlive it: short values SQLite keeps as index keys (a page's slug, made
  from a note's title; a project's name) can stay in index pages other workspaces still use;
  `compact()` (below) removes them.
- Erasure (H5b; cairn/store/erasure.py). `view.delete_project(project_id) -> bool` removes one
  project: its notes, spans, every version of its pages, its rows in the search index and
  statistics, its rule and the digests of its forgotten notes, and takes its id out of every
  key, code, grant and set that named it, and out of the source reports that declined it.
  False for an id unknown or out of reach (the same answer). The project's name leaves the
  config of every source that names it, and a source left with no project is switched off
  (store/sources.py's docstring): a project made later under that name gets nothing from it.
  `view.forget_note(note_id) -> Forgotten` (note_id, rebuilt, removed: page slugs)
  removes one note and its spans; each page any version of which cites one of them gets one
  new version (the next number) holding the latest version's claims that still have a span
  (a claim that cites several notes keeps the spans left, and goes when none is: see
  erasure.kept_claims; a kept claim whose id was made from a forgotten span gets another),
  titled with the `topic or title` of the oldest note left that its claims cite (the title
  it had may be the forgotten note's heading), its body rendered as the compiler renders
  it with that title, and every earlier
  version is deleted, since they held the note's text; a page left with no claim is deleted
  with its index rows. It stores the note's text digest in `forgotten` (project, SHA-256 hex):
  add_note of the same text into that project then raises ForgottenNote (a ValueError)
  before the guard runs and writes nothing, and save_page raises CitesForgotten (a
  StalePage) for claims citing a span that a forget removed meanwhile, so a compile that
  raced it reads the page again (compile_note of a forgotten note answers "unchanged").
  KeyError for a note unknown, out of reach or of a project out of reach.
  Who may: the person who made the project, or an owner, over a view that is not limited to
  named projects or read-only (`LIMITED_ERASER`), with no person the workspace itself; anyone
  else gets PermissionError (`NOT_AN_ERASER`) and nothing changes. `view.may_erase(project_id)`
  says so (KeyError when unknown) and `view.forget_preview(note_id)` says what a forget
  would rebuild and remove; both are reads. Each is one transaction with its audit event,
  project.deleted {"id", "name", "notes", "pages"} or note.forgotten {"note", "project",
  "pages"}, subtracts what it removed from `stored_bytes`, marks a compaction due (see
  Compaction state) and ends with the truncating checkpoint delete_account ends with, so the
  removed text is gone from the main file, -wal and -shm at once. Cost: both touch only the
  project's rows, through indexes, and never rewrite the database file; a forget reads every
  version of the project's pages to find those that cite the note, so it grows with the
  project's page history, and deleting a project grows with the project. Neither needs room under
  a plan's caps, and a rebuilt page keeps within `versions_per_page`.
  What the audit log keeps: every event stays, with its action, actor, time, the person's
  id, counts, kinds, codes and rule ids, and project.deleted keeps the project's id and name.
  Nothing made from a note stays: `page` (a slug, made from a title; erasure.NOTE_DERIVED,
  the only such key) is removed, in the same transaction, from every earlier event of a
  deleted project and from the events of each page a forget removes (a page rebuilt keeps its
  slug). A deleted project's events, project.deleted included, name
  audit.DELETED_PROJECT ("<id> (deleted)": a space no project name holds) instead of its name,
  so a later project of that name starts with none of them, and list_audit shows them only to
  owners and to views acting for no person, as WHOLE_WORKSPACE_EVENTS.
- Export (H5; cairn/store/portable.py has the layout). `view.export(out) -> ExportSummary`
  (projects, notes, pages: each page once, however many versions) writes a zip of
  everything the workspace's projects hold to a binary file object, format version 1:
  export.json {"format": "cairn-export", "version": 1, "exported_at", "workspace", "projects":
  [{"id", "name", "description", "kind", "created_at", "notes", "pages"}]};
  projects/<name>/notes/<NNNN>-<note id>.md, each note exactly as written;
  projects/<name>/notes.jsonl, one line per note, oldest first (id, title, topic, label,
  source_ref, via, created_at, file, sha256, topic_key, before_topics);
  projects/<name>/pages/<slug>.md, each page's latest body; projects/<name>/pages.jsonl, one
  line per page version in the order saved (slug, version, title, body, claims, metrics,
  created_at, topic_key). An
  import keeps both keys and the mark (N1); the notes waiting for their page are not
  marked so, but an archive written before N1 has its notes no page cites wait. Keys,
  sessions, grants, project sets, settings, sources and the audit log are not exported. It
  reads in one read transaction (one moment of the workspace) and writes nothing to the
  database, not even an audit event (the access log records an export made over the web);
  two exports of the same data differ only in `exported_at`. KeyError: the workspace was
  deleted. The summary's `notice` is None.
- Import (H5). `view.import_export(source) -> ExportSummary` restores such a zip (a
  seekable binary file object) into the view's workspace in one transaction, all or
  nothing: projects get new ids and keep their name, description, kind and creation time;
  notes keep their id and every field and time, so their spans, and every claim's
  citations, are the same (a field past FIELD_MAX, from an export written before the
  bounds, is cut to its bound: never the text, which is all a span cites); pages keep
  every version the archive has (from the oldest a plan kept, with its number), written in
  the order saved and indexed
  for search as save_page indexes them (search_projects and search_claims as the saves made
  them), so search works at once and ranks as in the source. It is recorded as one
  workspace.imported event (detail: the three counts and `fields_cut`, the note fields
  cut to their bounds, which the summary carries too, and `kept_from_models`, the projects
  that arrived at "never", M4). ValueError, with nothing written:
  not a zip, an unknown format or version, an archive past a bound or a name outside the
  layout, a manifest that does not match the files, a note that does not match its sha256,
  a claim citing a span that is not of its own project's notes, a claim that is not, whitespace
  aside, the text of a span it cites (a page cairn could not have written: the compiler's
  claims are its spans' text), a span cited from two pages of a project (in any versions:
  cairn compiles a note into one page), a version without claims, a claim whose id is
  not shaped as cairn makes one (portable.CLAIM_ID) or that cites a span twice, a version
  whose citations (its claims' span lists, added up) pass portable.CITATION_FACTOR (2)
  times its claims and the distinct spans it cites, added up, a version whose claims hold
  more than
  portable.CLAIM_TEXT_FACTOR (2) times the text of the distinct spans it cites (UTF-8
  bytes as the archive holds them, whitespace counted), a version whose body is not,
  whitespace aside, what cairn.compiler.render_body writes of its title, claims and the
  spans they cite (H6c: the body is stored as render_body writes it, and a title longer
  than a note's topic may be, FIELD_MAX["topic"], is cut to it), a version's metrics that
  are not a flat map of at most portable.METRICS_MAX names (1 to METRIC_NAME_MAX
  characters) to whole or finite numbers or texts of at most METRIC_TEXT_MAX characters, a
  page of more than PAGE_CLAIMS_MAX claims, a project name the workspace already uses, a note id the
  database already holds, or a note or page the note guard
  refuses (the same screening as add_note: `_screen`, then the policy in force inside
  the write; the message names the rules, never what matched, and nothing is recorded).
  An import takes what cairn writes (claims of whole passages, arranged and folded; since
  T5d the library's model composer, cairn.llm_compiler, passes the gate with nothing else);
  a page of claims that each select sentences of a span, which only a gate named in the
  verbatim gate's place lets through, is refused as a claim that is not the text of a span
  it cites. An archive is hostile input: it is never extracted to disk, and
  portable.py lists the bounds on what is read; `import_export(source, max_bytes=None)`
  reads at most 256 MiB
  (ARCHIVE_BYTES_MAX) of the archive file and of its entries, unless `max_bytes` says
  otherwise (ValueError below 1). The archive carries no access rules: every project it
  brings is private (`everyone` "none") and made by the view's person (maker_id; None for a
  view acting for no person, so then only the owners reach it), and the summary's `notice`
  says so in one sentence (portable.IMPORTED_PRIVATE; IMPORTED_OWNERS_ONLY with no
  person), followed by one sentence on the Models choice; the audit detail keeps the
  counts alone. It does carry each project's Models
  choice (M4): see "A project kept from every model" below.
- Compaction (H5). `store.compact()` rewrites the database file (VACUUM) and then truncates
  the write-ahead log (PRAGMA wal_checkpoint(TRUNCATE)): it gives the freed space back to the
  disk, and after it nothing of a deleted workspace, index keys included, is in the files. It
  rewrites every workspace's data and holds the writer's lock while it runs, so every other
  write waits (reads do not); it is the operator's: only the store that opened the database
  may run it (PermissionError on a view). CompactIncomplete (a sqlite3.OperationalError) when
  another connection kept it from finishing; running it again then finishes it.
- Compaction state (H5). `compaction_state()` says when a workspace (since H5b also a project
  or a note) was first deleted since
  the last compaction (`deleted_at`, None when none was) and when the last compaction began
  (`compacted_at`). It is two rows of the default workspace's `settings`, "store.compact_due"
  and "store.compacted_at", which get_setting and set_setting refuse and nothing audits:
  delete_account, delete_project and forget_note write the first in their own transaction
  (only for what existed, and only when not already set); compact deletes it and sets the
  second before it rewrites the file, and restores both when it raises CompactIncomplete.
  They survive a restart. `serve --compact-every` decides by them (cairn.web.compactor).
- `export`, `import_export`, `compact` and `compaction_state` are management calls (see
  Reach): a view limited to named projects, or read-only, is refused (PermissionError) and
  nothing is read or written. An archive is every project, so for a person `export` and
  `import_export` are an owner's calls: an admin or a member is refused
  (PermissionError(portable.NOT_AN_EXPORTER)) before the database is read or written, even
  when every project's rule lets them in, and so is a key they made (it acts for them); a
  view acting for no person, the workspace's own key among them, may.
- Reads never write: list/get/find/search/check/resolve calls leave the database
  byte-for-byte unchanged (no "last read" or "last used" bookkeeping), also when what they
  look at has expired.
- API keys, UI session tokens, authorization codes and OAuth tokens are random
  (`secrets.token_urlsafe(32)`), returned in plaintext exactly once, and stored only as
  SHA-256 hex digests. An API key made now starts with KEY_PREFIX ("cairn_"), so a scanner
  can recognise one; a key made before has none and works as it did, since a key is found
  by the digest of its whole value. An access token starts with ACCESS_TOKEN_PREFIX and a
  refresh token with REFRESH_TOKEN_PREFIX; both hold a ".", which a key or session token
  never does, so none of them can be taken for another.
- OAuth clients belong to no account and are not scoped. `register_client(name,
  redirect_uris) -> OAuthClient` (id "client_" + 32 hex chars; ValueError for a blank name
  or one over 80 characters, for no URI or more than 10, or for a URI that is empty or over
  255 characters; whether a URI is acceptable is the caller's policy). Each registration
  also removes the clients registered 7 days ago or earlier that never got a grant and
  have no code waiting.
  `get_client(id) -> OAuthClient | None`.
- A grant is one client's access to one account. `view.create_code(client_id, redirect_uri,
  code_challenge, redirect_uri_explicit=True, projects=None, access="write") -> str` is the
  account's approval, with the reach the person chose (ValueError as for `create_key`, and
  nothing is written): a code that lasts 2 minutes (KeyError for an unknown client or a view
  whose account is gone). The grant it is redeemed for keeps that reach for its life,
  across every rotation.
  `find_code(code) -> CodeInfo | None` reads an unexpired code. `redeem_code(code,
  client_id) -> TokenPair | None` makes the grant and its first access token (1 hour) and
  refresh token (30 days), once: None for an unknown or expired code or another client's,
  and presenting a redeemed code again also ends the grant it made.
  `rotate_refresh_token(token, client_id) -> TokenPair | None` replaces both tokens and
  starts a new 30 days; the old access token stops working and the old refresh token's
  digest is remembered for 30 days from the rotation, so presenting any token the grant
  rotated in that time ends the whole grant, and one rotated longer ago is unknown (None;
  nothing is written and the grant stays). The grant's next rotation removes such rows in
  the same write, and ending the grant removes them all. A grant rotates at most
  grants.ROTATIONS (6) times in any hour, counted from its rows in the rotating write; one
  more raises grants.TooManyRotations and nothing is written. So a grant remembers at most
  6 x 24 x 30 = 4,320 rotated tokens. `refresh_token_client(token)` names the client of a
  current or remembered refresh token. `revoke_token(token, client_id) -> bool` ends the
  grant any of that client's live or remembered tokens belongs to. A grant also ends when
  its refresh token expires.
  `create_code(..., audience=None)` binds the code, and the grant it is redeemed for, to an
  audience: the MCP address it was approved at. `redeem_code`, `rotate_refresh_token` and
  `identify_token` take `audience=` too, and then find only a code or grant bound to it or
  to none (the server always names one); a mismatch is unknown and writes nothing.
- `resolve_token(access_token)` looks in every account and returns Principal(account_id,
  kind "token", label: the client's name, user_id: the person who approved, or None,
  projects and access: the grant's reach) or None; it is a read. A code made by a view acting
  for a person hands that person on to the grant it is redeemed for. `view.list_grants() ->
  list[GrantInfo]` (id "grant_" + 12 hex chars, client_id, client_name, created_at, projects,
  access; live grants, oldest first; never a digest) and `view.revoke_grant(id) -> bool`
  only see the view's own account.
- Expired codes, access tokens and grants are skipped by reads and removed by the next
  OAuth write (registering, approving, redeeming, rotating, revoking), never by a read.
- The sweep (H5c; store/expiry.py). `sweep_expired() -> int` removes, in one write, every
  row whose time has passed, in every workspace: sessions and invitations at their
  expiry, codes, access tokens, rotated refresh tokens past their 30 days, grants whose
  refresh token has expired with all they hold, and the clients registered 7 days ago or
  earlier that never got a grant and have no code waiting. It returns how many rows it
  removed; a row whose time has not come stays. With nothing to remove it returns 0 and
  the file does not change. It reads the clock once it holds the write lock, records no
  audit event, and only the store that opened the database runs it (PermissionError on a
  view). `serve` runs it every 10 minutes (cairn.web.sweeper).
- People. A user (id "usr_" + 12 hex chars, email, name, created_at) signs in through
  identities (provider, subject) and is a member of workspaces with a role, "owner", "admin"
  or "member". One user per email address, compared ignoring case. `get_user(id) -> User |
  None` is not scoped. `view.list_members() -> list[Member]` (user_id, email, name, role) is
  the view's account's, in joining order. `view.role() -> str | None` is the role there of
  the person the view acts for; None for a view that acts for no person.
  `sign_in(provider, subject, email, name, *, issuer, join, ttl_seconds=43200) -> SignedIn`
  (the caller has checked that the provider verified the email) runs in one transaction: the
  identity names the user, and records `issuer`, the provider's issuer URL; an identity
  known under another issuer raises ProviderChanged (the provider was replaced under the
  same name, so the subject may be someone else's) and nothing is written; a new identity
  whose email is a user's is that user; otherwise a new user is made, named `name`
  (whitespace collapsed, cut to 80 characters; the email when blank). A user with no
  workspace joins one: "personal", a new workspace named after them that they own;
  "default", the default workspace, as owner when it has no member yet and as member
  otherwise; but someone removed from it (a removal marker for it and their address, or
  for it and their identity, the one signing in included, see `remove_member`) gets in
  only by an open invitation for their address, which this sign-in takes (its role,
  member.joined under them, the markers deleted; the answer's `invited` is then True), and
  otherwise raises
  Removed (a PermissionError whose message tells them to ask an owner for an invitation)
  and nothing is written. A returning user keeps everything as it was, name included. The
  answer holds a new session (token, shown once) for the user in their first workspace in
  joining order, the user, the workspace and `created` (whether this sign-in made the
  user). ValueError for a blank provider, subject or issuer, an email without "@" between a
  local part and a domain, or another `join`, and then nothing is written.
- Managing people (T1). One rule decides who manages a workspace (StoreBase._manages, asked
  inside each call's own transaction): a view acting for no person, or a person whose role
  there is now "owner" or "admin" (MANAGER_ROLES); `view.manages() -> bool` answers it, and
  is False on a narrowed view. Every call below, and keys, sets, settings and sources,
  raises PermissionError for anyone else, and only an owner makes, unmakes or removes an
  owner. A workspace always has an owner: removing or demoting the last one is a
  ValueError, decided in the write transaction, so two demotions at once leave one.
  - `view.invite(email, role="member", ttl_seconds=INVITE_TTL) -> Invite` (id, email,
    role, created_at, expires_at, and the token, given this once: the store keeps its
    SHA-256 digest). ValueError for an address without "@" between a local part and a
    domain, a role not in ROLES or a negative lifetime. Inviting an address again replaces
    its open invitation; each invitation deletes the workspace's expired ones.
    `view.list_invites() -> list[OpenInvite]` (open ones, no token) and
    `view.revoke_invite(id) -> bool`.
  - `find_invite(token) -> InviteInfo | None` (account_id, workspace, email, role) for an
    open invitation; a read. `accept_invite(token, user_id) -> Member` takes it once:
    KeyError for a token that is unknown, used, revoked or expired, or an unknown user;
    PermissionError when the user's email is not the invitation's, compared ignoring case
    and nothing else; nothing is written on either. A person already a member keeps their
    role (the invitation is used up). Recorded as member.joined in the invitation's
    workspace, under the view's actor. Taking it deletes the person's removal markers for
    that workspace: their address's and each of their identities'.
  - `view.set_role(user_id, role)`: ValueError for a role not in ROLES, KeyError for
    someone who is not a member; setting the role they have writes nothing.
  - `view.remove_member(user_id) -> bool` (False for someone who is not a member) ends, in
    one transaction, their sessions here, the agents they approved here (grants, codes and
    tokens), the keys they made here and their place in this workspace's project rules; the
    projects they made keep no maker. What they wrote stays. A person left with no workspace
    is removed with their identities. It leaves removal markers: the SHA-256 digest of
    the workspace id and their lower-cased address, and one of the workspace id and each
    identity (provider, issuer, subject), so a changed or re-verified address does not
    let the same identity back; never the address or the subject itself. They are kept
    until that person takes a fresh invitation or the workspace is deleted, so signing in
    does not undo them. A view still held for them (a request admitted just before) writes
    nothing: every write checks, inside its own transaction, that the view's person is a
    member now (StoreBase._write, the one check), and refuses otherwise (PermissionError,
    whatever ids it names), project creation, sessions, codes, keys, sets, settings,
    invitations and project rules included; only ending a session is let through. Their
    reads see no project.
  - `list_workspaces(user_id) -> list[Workspace]` (account_id, name, role), in joining
    order; a read.
- Project names match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, are unique case-insensitively within
  an account (not across accounts), and may not start with "br_" in any case (the id prefix),
  so a name never reads as an id (`is_project_name(name)` says whether a name passes, taken
  or not). `get_project` accepts an id or a name (name match is case-insensitive).
- A project's description holds at most DESCRIPTION_MAX (500) characters, for every caller:
  `create_project` raises DescriptionTooLong (a ValueError) past it and makes nothing, and an
  import cuts a longer one and counts it among `fields_cut`. No cap counts a description,
  so it has a bound of its own.
- Project kinds (U1). `create_project(name, description="", kind="general")`: `kind` is an id of
  cairn.kinds.KINDS, what the project is for (ValueError for any other, checked first, and
  nothing is written), and `Project.kind` holds it. Kinds are data: the schema lists none, so
  adding one needs no migration. Projects made before kinds existed are "general".
  `create_projects([(name, kind), ...]) -> list[Project]` makes them in one transaction, all or
  none (a management write): a project that already has the name and kind is kept and
  returned in its place; an invalid name or kind, one name twice, or (ProjectTaken, whose
  `project` is the one in the way) a name a project of another kind has is a ValueError, and
  nothing is written. A name a project the view does not reach already has is NameTaken
  (ProjectTaken's parent, a ValueError), from either call, with no project handed back and
  nothing else of it told; `create_project` raises NameTaken for any taken name. This is the
  one thing a hidden project gives away: names are unique per workspace, so a person who does
  not reach a project can learn that its name is taken. The server answers every taken name
  with one sentence, seen or hidden (cairn.memory.name_unavailable), and says nothing more.
- Who reaches a project (T1). `create_project(name, description="", kind="general", *,
  everyone=None)`: `Project.maker_id` is the person the view acts for, else None, and
  `everyone` is what any member gets, "write", "read" or "none" (LEVELS; ValueError
  otherwise); None, and every project `create_projects` makes, takes the kind's default
  (`kind_everyone`: "none" for a kind that is private, cairn.kinds.Kind.private, which
  only "personal" is; else "write"). `view.project_access(project_id) -> ProjectAccess`
  (maker_id, everyone, people: user id to "read", "write" or "manage") and
  `view.set_project_access(project_id, *, everyone=None, people=None) -> ProjectAccess`, which
  replaces what is given (None: as it is) and writes nothing when nothing changes. A
  person who is not an owner taking `everyone` to "none" takes the project out of every
  source that names it, as deleting it does (access.PROJECT_PRIVATE).
  `view.project_sharing(project_id) -> ProjectSharing` (everyone, people: the user ids the rule
  names, its maker included, whatever their level) is how far a project is shared, for anyone
  who reaches it (KeyError otherwise, as for an unknown project), so the web view can say a
  project is private to the people who see it. The first two: KeyError for a project out of the
  view's reach, PermissionError for a person who is neither the project's maker, nor an owner,
  nor at "manage" on it (a view acting for no person may; a workspace admin is a member
  here); and for the setter ValueError for a level not allowed (`everyone` is never
  "manage"; a person's level is one of PERSON_ACCESS) or a person who is not a member,
  checked before anything is written. "manage" (T2) is everything "write" is, and deciding
  who reaches that project: its holder may give and take any level, "manage" and their own
  included, but an entry never limits the maker or an owner, who reach the project whatever
  it says. It does not delete the project or forget a note: those stay with the maker and the
  owners (Erasure, above). `view.share_project(project_id, level, emails) -> Shared` gives one
  level ("manage", "write" or "read") to every member of the workspace whose address is in
  `emails` (compared as users.email is, ASCII letters in either case, outer spaces
  dropped), and leaves `everyone` and every other person's entry as they were: `granted`
  holds their user ids in the order typed, each once (the maker's and an owner's included,
  with no entry written for them), `unknown` the addresses that are no member's, as typed
  less outer spaces, each once. Those change nothing and invite nobody: someone outside the
  workspace is invited under People first. The same callers as set_project_access, and one
  project.access_changed, with the same detail, for a call that changed something. ValueError,
  before anything is read, for a level not one of the three, a list that is one string,
  empty or longer than SHARE_MAX (200), or an entry that is not one address (blank, no "@",
  a space inside, longer than ADDRESS_MAX), named by its position and never by what it
  holds. A person reaches a project
  with the most any of these gives: owners write everywhere, the maker writes, `people`,
  `everyone`. That reach is resolved at each call, inside its transaction, through the one
  seam narrowed views use (StoreBase._reach: the account, then the person, then the
  connection), so a project a person does not reach behaves for them exactly as one out of a
  narrowed view's reach (below), and a person who has left reaches nothing. With "read",
  a write into the project raises PermissionError (READ_ONLY_PROJECT, which says "read-only").
  A person's view shows keys, grants, codes, sets and sources only as far as they reach:
  an id of a project out of reach is left out of a reach or a set it is in, refused (as
  unknown) in one being made, and kept when a person replaces what they see of a set; a
  source whose config names such a project is shown to an owner or admin without that project
  (SourceInfo.unreached: Sources, below), and is unknown to a run on their view. A member
  sees and ends only
  the agents they approved; managers see all.
- A project kept from every model (M4, frozen by
  tests/acceptance/test_m4_a_project_no_model_reads.py). A project's pages may be sent to a
  model the operator connects ("allowed", the default) or never ("never"): MODELS.
  `view.set_project_models(project_id, models) -> str` sets it and gives the state now:
  ValueError, before anything is read, for any other value; then KeyError and
  PermissionError exactly as set_project_access has them (the same callers: the maker, an
  owner, a person at "manage", a view acting for no person), and a narrowed view is refused
  (reach.MANAGEMENT). A change is one project.models_changed event (the project by name;
  detail: `project_id` and `models`, and `user` as every person's event has it); setting the
  state a project has writes nothing. `view.kept_from_models(project_ids) -> frozenset[str]`
  gives the ids among them, in reach, at "never", in one statement whatever their number.
  `view.page_for_model(project_id, slug) -> (Page, bool) | None` is get_page's latest version
  and whether its project is at "never", read in that one statement (cairn.arrange learns it
  at no cost of its own). It is kept with no change to the schema, as one row of the
  workspace's settings per project at "never" (access.MODELS_PREFIX and the project's id;
  "allowed" is no row), bounded by the workspace's projects; get_setting and set_setting
  refuse the name. A project whose kind is private (cairn.kinds.Kind.private: "personal")
  starts at "never", whoever makes it and however (create_project, create_projects,
  import_export), in the write that makes it; every other project starts at "allowed".
  delete_project removes the row in its own write, and a deleted workspace takes its
  settings with it. Projects are never renamed, and the row names the id, not the name. An
  export carries this one setting, each project's `models` in its manifest: an import puts
  a project the archive says is at "never" there, in its own write, and a project of a
  private kind at "never" whatever the archive says; an archive written before says it of
  no project, and each starts as a new one of its kind (portable.py says the notice and the
  audit count). Existing projects are not touched: nothing was migrated.
  The state holds at the moment of a call: `view.reserve_model_use(micros, project_id)`
  reads it in the reservation's own transaction and raises KeptFromModels, writing nothing,
  for a project at "never"; `save_page(..., by_model=True)` reads it in the statement that
  reads the page's latest version and raises KeptFromModels, writing nothing (cairn.arrange
  passes it for a model's answer, never for a tidy).
- `add_note` stores the note and its spans, chunked with source_id = the note id.
  Title: the given title, else the frontmatter `title`, else the first ATX heading's text,
  else the first non-blank line, stripped and cut to 80 characters. A note is a duplicate
  when the project already holds one with identical text and the same source_ref (None counts
  as a value), found by the index notes_by_text; then the existing note is returned
  with created=False and nothing is written, unless it was stored for another topic key
  after N1 (below): then FiledElsewhere (a ValueError; `.held`, the note held) and
  nothing is written. A note stored before N1 (`Note.before_topics`, set by schema
  step 24 and by an import of an archive written before) is the same note whatever topic or
  title it is sent with. Each field beside the text is bounded in characters (FIELD_MAX:
  title and topic 200, label 80, source_ref 500, via 40): a longer one is FieldTooLong,
  a ValueError naming the field, before anything is screened or written; `import_export` cuts one an
  archive holds to its bound instead (an export written before the bounds still imports).
  The headings above each span of the text (its heading path; a heading's own path holds
  it too) take at most HEADINGS_MAX (400) UTF-8 bytes in all, counted as stored (JSON
  written as UTF-8, so a quote or backslash counts 2, a control character 6): past it,
  HeadingsTooLong (a FieldTooLong whose `field` is "headings", naming the bound, never a
  heading), checked the same way, before anything is screened or written. Why 400: a
  heading is stored with every span under it, and a passage costs about 380 bytes of rows
  whatever its length, so its headings may cost about as much again and no more. And a
  note's heading spans may outnumber its passages by at most HEADINGS_OVER_PASSAGES (8):
  past it, TooManyHeadings (a HeadingsTooLong giving the two counts and the bound, never a
  heading), the same way; an outline of a few headings with nothing under them still goes
  in. Never cut: a page's headings come from these paths, and a cut one is a heading
  nobody wrote, so `import_export` refuses an archive with a note past either (ValueError
  naming the note's line, quoting nothing).
- A note holds at most FACTS_MAX facts (G1; cairn.facts: numbers, dates, versions, paths,
  links, flags, inline code and code blocks), counted by `count_facts` as extract_facts
  finds them, without building one: past it, TooManyFacts (a ValueError giving the count
  and the bound, never a fact), checked after the headings, before anything is screened or
  written, and `import_export` refuses an archive with such a note the same way. Why: a
  compile's time and memory grow with a note's facts, and nothing else bounded them.
- The note guard (G1) is enforced in `add_note`, the one place a note is written:
  every field it stores (SCREENED_FIELDS: text, title, topic, label, source_ref, via)
  is screened, by the view's own guard when it has one (`view.with_guard(guard) -> Store`,
  the same view with every narrowing, as `create_app(..., guard=...)` gives each request),
  else by cairn.guard's PatternGuard under the workspace's "guard.policy" (default
  DEFAULT_GUARD_POLICY; read whatever the view's reach). Patterns run before the write
  transaction under the widest policy; inside it, the policy in force then decides which
  findings count (POLICY_KINDS), so a policy changed meanwhile is the one that decides. A
  finding raises NoteRefused(findings: [(field, Finding)]) after recording note.refused
  (detail: the rule names) and storing nothing else; an unknown or unreachable project is
  a KeyError first, and a read-only view a PermissionError before anything is screened.
  `view.screen(text) -> list[Finding]` (T4b) is the same two steps for text the store
  already holds: what the view's guard, or else the workspace's policy as it is now, would
  refuse. A read, answered alike by every view (cairn.arrange asks it before a page's
  claims go to a model, since a policy tightened later does not reach notes taken before).
- Every `save_page` writes a new version (1, 2, ...); earlier versions stay readable.
  A page holds at most PAGE_CLAIMS_MAX (2,000) claims (H6c): `save_page` with more raises
  PageTooLong (a ValueError naming the page and the bound) and writes nothing, and
  `import_export` refuses (ValueError) an archive with a version past it. cairn.memory
  refuses a note that would take its page past it before storing the note, with
  `note_title(given, text)` (the title add_note gives a note) and `holds_note(project_id,
  text, source_ref=None)` (whether add_note would answer "unchanged"; a read, False for
  a project out of reach).
  A page's claims hold at most PAGE_CHARS_MAX characters in all (G1; `claim_chars`): a
  note's save (event note.saved) past it raises PageTooLong with `chars` set, naming that
  bound, and writes nothing. Saves that are not a note's (page.arranged) and forget's
  rebuilds are not refused, so a page already larger when the bound came stays as it is
  and can be arranged and forgotten from. An import is not refused for it.
- Topics (N1, frozen by tests/acceptance/test_n1_note_filing.py; the rule is cairn.topics).
  `add_note` makes a note's topic key once, from its topic, else its title as given,
  else the title its text gives, each up to FIELD_MAX's 200 characters, and keeps it
  (`Note.topic_key`; `note_key(text, title, topic)` computes it as add_note does,
  `stored_key(note)` reads it). A page with a key keeps it: `save_page(..., topic=key,
  filed=note_id)` for another key raises StalePage and writes nothing, so the compile
  chooses its page again. A page with none takes the key it is saved for (without `topic`,
  its title's), unless another page of the project holds that key: then a save for it raises
  StalePage, and a save without `topic` goes on, leaving the page keyless.
  `topic_page(project_id, key)` is the slug of the page of `key` (None: none yet),
  `taken_slugs(project_id, slugs)` the slugs of the project among `slugs` (both reads; nothing
  for a project out of reach). A note is
  waiting from the moment it is stored until its outcome is recorded (`record_note(...,
  filed=note_id)`, in the same write): `waiting_notes()` lists them (WaitingNote:
  note_id, account_id, workspace, project_id, project, stored_at, tries; never a text or
  a title; every workspace's, the operator's call, reach.OTHER_WORKSPACES), and
  `tried_waiting(note_id)` counts one more try. Forgetting a note, deleting its project
  or its workspace removes its waiting record and the keys of the pages that go. Schema
  step 24 gave each page made before N1 the key of its latest title and each note the key
  of the page that cites it (else its own), with its own copy of the rule, marked every
  note `before_topics`, and made every note that no page's latest version cites wait.
  `save_page(..., expected_version=n)` (T5, frozen by tests/acceptance/test_t5_grounding.py)
  saves only when the page's latest version is `n`, 0 for a page that does not exist yet,
  and otherwise raises `StalePage` (its `expected` and `found` versions) and writes nothing,
  search index included. The check and the write are one transaction, so two writers on
  one page, through one `Store` or several on the same file, never both build on one
  version. An unknown project is still KeyError. Without `expected_version` it behaves as
  before.
- `search` matches the latest version of each page (claim text and page title) in SQLite
  FTS5, one hit per matching claim, best first (score: positive, higher is better; ties in
  the order claims were indexed). Any user text is a valid query: its first 32 word tokens
  (WORDS_MAX), each quoted, are joined with OR (a hit matches at least one); later ones are
  ignored, and nothing is ever passed to FTS5 as query syntax. No tokens, an empty
  `project_ids` or a limit below 1: no hits, and the database is not read. `project_ids=None`
  searches every project the view reaches; `project_ids` only filters hits.
  A search's hits and scores depend only on the projects the view reaches, through one code
  path for every view (cairn.store.ranking): BM25 with FTS5's formula and constants (k1 1.2,
  b 0.75, its IDF and floor), where N, the average claim length and each word's document
  frequency count the claims of those projects alone. A view of every project ranks as FTS5's
  bm25() ranks the account's whole index (scores equal up to the last bits of floating-point
  rounding), and a view limited to named projects as bm25() would rank an index of only those
  projects; nothing out of reach, another account included, moves a score. Only as many
  matching claims as a fixed amount of work allows for the parts of its words are scored
  (ranking.scored_max: 8,000 for one part, 4,000 for two, 1,000 from eight on), those
  likeliest to score highest: a match beyond them is not returned, and the scores of those
  that are do not change. Each account has its own FTS5
  table, "claim_search_" + the account id, made by the account's first `save_page` and
  dropped with the account; until then a search finds nothing and creates nothing.
  `save_page` keeps each project's claim and token counts (`search_projects`) and each claim's
  project and length (`search_claims`) in its own transaction, so no search counts a corpus,
  and a match in a project out of reach is dropped without its claim being read.
- Project sets. A set is a named group of a workspace's projects: `create_set(name,
  project_ids=()) -> ProjectSet` (id "set_" + 12 hex chars, name, project_ids in the order given,
  each once, created_at). Names follow the project-name rules, may not start with "set_" either
  (so a name never reads as an id), and are unique per workspace ignoring case. ValueError
  for a bad or taken name, or an id that is not a project of the workspace (a set holds projects,
  never sets). `list_sets()` (by name, ignoring case), `get_set(id or name)` (an id match
  wins; None for an unknown one or another workspace's), `update_set(id, project_ids) ->
  ProjectSet` (replaces its projects; KeyError for an unknown set, ValueError as above) and
  `delete_set(id) -> bool` only see the view's own workspace. A set shows only the projects
  that still exist.
- Reach. Every key and grant has one: `projects`, None for every project of the workspace, now
  and later, or a tuple of ids of the workspace's projects and sets; and `access`, "write" or
  "read". It is stored as given and resolved at each call: a reach that names a set covers
  whatever the set holds then, and a project or set deleted later drops out of it silently.
  `create_key` and `create_code` refuse (ValueError) an access other than those two, a
  reach that names nothing, and an id that is not a project or set of the workspace (a name is
  not an id).
- Narrowed views. `view.narrow(projects=None, access="write") -> Store` is a view of the same
  workspace, made as `for_account` makes one, limited to that reach and to every reach the
  view already had: the projects it covers are the intersection of each, resolved at the time
  of each call, and it is read-only once any narrowing was, so narrowing can only narrow
  (`for_account` on a narrowed view keeps the narrowing for its own account, and raises
  PermissionError for any other: a reach names one workspace's ids). Ids are not checked:
  one that names nothing covers nothing. Through a narrowed view:
  - a project out of reach, and every note, span and page in it, behaves exactly like one
    that does not exist (None, [], {} or KeyError, as for unknown ids);
  - `search` covers only the reach, also with `project_ids`, and ranks with statistics over
    the reach alone (see `search`): its scores are exactly those of an account holding
    only those projects;
  - with "read", every write raises PermissionError (its message says "read-only"); writes
    into a project check this first, so a read-only view refuses a write to an unknown project
    the same way;
  - only a view that reaches every project with write access manages the workspace: on any
    other, creating projects, keys and sets, changing or deleting sets, revoking keys, making
    or ending sessions, approving clients (`create_code`), ending grants, changing who
    reaches a project (`set_project_access`, `share_project`; neither the REST API nor an MCP tool
    has a call for either, whatever the credential's person holds), and the account,
    sign-in and OAuth writes all raise PermissionError (its message says "limited" for a
    view limited to named projects, read-only or not, else "read-only"), and nothing is
    written;
  - a view limited to named projects sees none of the workspace's keys, grants or sets (they
    name projects it must not learn of): `list_keys`, `list_grants` and `list_sets` are [] and
    `get_set` is None;
  - lookups by credential (`check_*`, `resolve_*`), OAuth reads, accounts, people and roles
    are as on any view.
  - settings are neither read nor changed (`get_setting` and `set_setting` raise
    PermissionError as a management write does), and `list_audit` is [], also on a view
    that is only read-only;
  - `record_note` is a write into a project, as above.
  cairn/store/reach.py classifies every public method this way, and a unit test checks
  that none is left out. A call on a project's data resolves the reach once, inside the
  transaction it reads or writes in, so a set changed meanwhile is seen either wholly before
  the call or wholly after it.
- Settings (G1). `view.get_setting(name, default=None) -> str | None` is the workspace's
  value, or `default` when it was never set; `view.set_setting(name, value)` sets it. Only
  known settings with the values each accepts (SETTINGS): "guard.policy", one of
  GUARD_POLICIES ("off", "secrets", "secrets+personal"; add_note reads it). Anything
  else is a ValueError, and nothing is written. Setting the value a setting already has
  writes nothing. Changing a setting is a management action: a view acting for a person
  whose role is not owner or admin (SETTING_ROLES; checked inside the write) gets
  PermissionError; a view acting for no person may. Settings are per workspace and deleted
  with it.
- Actor (G1). Every view acts as someone: `view.actor` is (kind, name), ("system", None) for
  a view used directly, as the command line does. `view.acting_as(kind, name,
  actor_id=None) -> Store` is the same view (its account, person and every narrowing)
  acting as kind "key" (name: the key's label), "token" (the agent's client name),
  "session" (the person's name, or the label of the key the session was opened with) or
  "system" (ValueError otherwise), with `view.actor_id`, which tells apart actors that
  share a name (labels and client names repeat): the key's id, the grant's id or the
  person's. `identify_key(key)`, `identify_token(token)` and `identify_session(token)`
  return (the resolve_* Principal, its actor id): a key's id ("key_" + 12 hex chars, never
  reused), the grant's id, and for a session the person's id, else the id of the key it was
  opened with, else None. `create_session` on a view acting as a key records that key's
  label and id on the session.
- Writers (A1; frozen by tests/acceptance/test_a1_written_by.py). Every note records its
  writer as it is stored, from the view that stores it and never from the note:
  `view.writer` is (kind, name), set by `view.writing_as(kind, name, key_id=None) ->
  Store` (the same view, every narrowing kept; ValueError for a kind not in
  audit.WRITER_KINDS: "person", "key", "agent", "source", "operator"; a person's name goes
  through audit.person_name, so it holds no address), else taken from the actor: a key by
  its label, an agent ("token") by its client's name, a session by its person's name, and
  any other view (the command line) as "operator" with no name. A note sent again keeps its
  first writer. `view.note_writers(note_ids) -> {id: (kind, name, via)}` reads them
  for the notes the view reaches, "unknown" with no name for a note stored before writers
  were recorded. Erasure, deleting a project or a workspace remove a note's writer with it;
  an export carries it (portable.py says how) and an import restores it.
- What a key made (A1). `view.made_through` is the id of the key the view's caller came
  with (writing_as's `key_id`, else the actor's id when it acts as a key): `create_key`
  and `create_code` record it for what they make (made_with), and `redeem_code` hands the
  approval's to its grant. `list_keys` and `list_grants` name that key by label
  (`made_with`), or say it is revoked (`made_with_revoked`); a key made before the moment
  in recorded_since has `unrecorded_before`. `view.made_by(label) -> Made` (a manager's
  read) is what the keys of that label made, at any depth, by the ids recorded.
  `revoke_key(label, with_made=True)` takes all of it in the same write
  (credentials.revoke_key says what it records).
- Audit log (G1). Each write records one event in the workspace it belongs to, under the
  view's actor, in the transaction of the write itself (a write that fails or is refused
  records nothing): project.created (`create_project`; detail: the kind, and `everyone` when
  it is not "write"), key.created
  (`create_key`; detail: the label, and the reach when it is not every project to write),
  key.revoked (`revoke_key`, when a key was revoked), setting.changed (`set_setting`;
  detail: the name), note.saved (`save_page` for a compiled note; detail:
  page and version), page.arranged (T4b: `save_page(..., event="page.arranged",
  detail=...)`, cairn.arrange's save of a model's order of a page's own claims, or of a tidy
  with no model (T4f, model cairn.arrange.TIDY); detail: page, version, from_version, model,
  moved and merged; PAGE_EVENTS are the two a save records, and
  PAGE_DETAIL the only fields and kinds each takes from its caller: counts are ints of 0 or
  more, the model's name 1 to 80 printable characters; anything else is a ValueError),
  note.refused (`add_note`, when the guard refuses; detail: the
  rules), note.unchanged and note.rejected (`record_note(project_id, outcome, detail)`, for
  the outcomes no other write records; `Memory.note` calls it, and records note.rejected
  with the violation "compile.error" when the compile raises, so a stored note always
  has an outcome), agent.connected
  (`redeem_code`, under ("token", the client's name) in the workspace that approved;
  detail: the grant, the approving person and the reach when not every project to write) and
  agent.disconnected (`revoke_grant` under the view's actor; `revoke_token` and a replayed
  code or refresh token under the agent, with the reason), source.added (detail: source and
  kind), source.removed (detail: source, refiled as below, and its name) and
  source.synced (`finish_source_run`, unless every count is zero; detail: the source and
  its counts, `malformed` among them), and (T1) member.invited (detail: the invitation's
  id, email and role; never its token), invite.revoked, member.joined,
  role.changed (detail: member, from, to), member.removed (detail: member, role, and how
  many keys and agents ended) and project.access_changed (detail: everyone and people), and
  set.created, set.changed and set.deleted (`create_set`, `update_set`, `delete_set`;
  detail: the set's name and `projects`, the ids it holds after the change, or held), and
  (B1) sessions.ended (`end_person_sessions`, as ("system", None), in each workspace where
  a session ended; detail: member, the person's id, and sessions, the count; never the
  address), and (A1) session.opened (`sign_in`, under the person, in the workspace it
  opens; `create_session` on a view acting as a key, under the key; detail: `how`, the
  provider's name or "key"), agent.approved (`create_code`, under the view's actor, as the
  consent is given; detail: agent, the client's name, client, its id, redirect_uri, and the
  reach when not every project to write; never a grant, there is none yet; agent.connected
  carries `client` too) and key.revoked_with_made (`revoke_key(..., with_made=True)`, when
  it took anything; detail: label, keys, agents). A view acting for a person adds
  "user": their id to the detail. Nothing else is recorded: not a session opened any
  other way, accounts, notes on their own, OAuth clients, token rotation or expiry.
  Reads are never recorded (reads never write).
  `view.list_audit(limit=100) -> list[AuditEvent]` (at,
  actor_kind, actor, actor_id: as above, None for system and for events recorded before
  schema 7, action, project: the project's name or None, detail: names, ids and
  counts, never note text, a secret or a digest) is the workspace's, newest first, at most
  AUDIT_LIMIT_MAX; a limit below 1 gives []. For a person who does not reach every project,
  an event of a project out of their reach is left out before the limit, and such a project's
  id is left out of an event's `projects`. A person who does not own the workspace is
  filtered so whatever they reach, with every existing project as their reach when they
  reach them all: a removed source's events, and events naming no existing project, are not
  theirs. A live source's events are every owner's and admin's, and no member's; the project
  names in a source event's `projects` are filtered as they are read, old events included:
  only projects that exist now and the viewer reaches are named, and a deleted one only to
  a view acting for no person (cairn.store.audit.list_audit). remove_source refiles every
  event of the source, source.removed included, under audit.REMOVED_SOURCE ("<name>
  (removed <hex>)": a space no source name holds, and a word unique to the removal) instead
  of its name, in the same write, so a later source of that name starts with none of them,
  and list_audit shows them only to owners and to views
  acting for no person, as DELETED_PROJECT. An event with no project that describes the whole
  workspace's content, counting projects, notes or pages private ones included
  (audit.WHOLE_WORKSPACE_EVENTS: workspace.imported, the only one), is shown to owners and
  to views acting for no person only, never to an admin or a member, whatever they reach.
  Not under it: source.synced (counts one source's run into the projects its config names,
  and is shown only with its source) and member.removed (counts keys and agents, not
  content). The log is deleted with the workspace. It is
  bounded: a workspace keeps at most its newest AUDIT_KEEP (100,000) events, since anyone
  who may write can make events (a refused note is one). An insert past that deletes the
  oldest in its own transaction, AUDIT_PRUNE_BATCH at a time, so most inserts delete
  nothing and right after one that does, the newest AUDIT_KEEP - AUDIT_PRUNE_BATCH + 1
  remain.
- Sources (C2; cairn.sources has the runner). `add_source(name, kind, config) ->
  SourceInfo` (name, kind, config, cursor, created_at, last_run_at, last_report,
  last_error, enabled, enabled_at): names follow the project-name rules and are unique per
  workspace ignoring case;
  `kind` is a name of cairn.sources.KINDS, which checks the config (the store keeps what the
  kind returns, and never a credential: a config names environment variables); every project
  the config's `projects` names must be one of the workspace's. ValueError otherwise, and
  nothing is written. `list_sources()` (by name), `get_source(name)` (ignoring case; None
  when unknown) and `remove_source(name) -> bool` only see the view's own workspace; what a
  source noted stays when it is removed. A view acting for a person who is not an owner or
  admin sees no source and may not add, remove or run one (PermissionError); a narrowed
  view, read-only included, sees none and manages none (Reach). Every owner and admin sees
  every source (list_sources, get_source) and may switch any one off; where it writes is
  shown only as far as the view reaches (the config's `projects` without the others, and
  SourceInfo.unreached set): store/sources.py's docstring has the rule. A source whose
  config names a project the view does not reach is unknown to it in every call of a run and
  in remove_source, decided in that call's transaction: remove_source False, claim_source
  KeyError (its config is never handed out), finish_source_run, fail_source_run and
  holds_source_claim False with nothing written. The last report's `declined` list
  keeps only the drafts (each names its `project` id) whose project the view reaches; one with
  no project recorded is shown to a view of every project only. Sources are deleted with
  their workspace.
- On and off (S2; store/sources.py's docstring has the rules). A source of a kind that
  starts off (SourceKind.starts_off) is added off; any other is on. `enable_source(name,
  projects=None, *, new_project=None) -> SourceInfo` and `disable_source(name) -> SourceInfo`,
  for a view acting for no person, an owner or an admin, refused on a narrowed view,
  audited as source.enabled and source.disabled; a call that changes nothing writes
  nothing.
- Runs. `claim_source(name) -> SourceClaim | None` (name, token, kind, config, cursor,
  enabled_at) is a
  compare-and-set in the database: it takes the source when no run holds it, or when the
  holder's claim is SOURCE_CLAIM_TTL (15 minutes) old, so a run killed while it held one
  blocks the next for at most that long; None when another run holds it; KeyError for an
  unknown source; SourceOff (store/sources.py), with nothing written, for one that is off.
  `finish_source_run(claim, cursor, report) -> bool` stores the cursor, the
  report (a JSON object), clears `last_error`, sets `last_run_at` and releases the claim,
  in one transaction with its audit event; `fail_source_run(claim, error) -> bool` stores
  the error (cut to 1,000 characters) and `last_run_at` and releases the claim, leaving the
  cursor and the last report. Both write nothing and return False when the claim is no
  longer that run's (another took it after the limit, or the source was removed).
- Metering (H6; store/usage.py), always on, hosted or not. `view.usage(period=None) ->
  Usage` (`period`, `notes`, `note_bytes`, `model_tokens_in`, `model_tokens_out`,
  `model_micros`) is the workspace's use in the UTC month `period` ("YYYY-MM", PERIOD; the
  clock's current month when None; ValueError for another shape), zero for a month with
  none. A note is counted in the transaction that stores it, after the guard and the
  duplicate check, in the UTC month of the instant it is stored at (its `created_at`): one
  note and its UTF-8 size; the same note again ("unchanged") counts nothing.
  `view.stored_bytes()` is the UTF-8 size of every note the workspace holds, whenever it
  was written: a running total each stored note adds to, never a count of the notes (so a
  later deletion of notes must subtract what it removes). `view.passages()` (H6c) is what
  the passages cap charges for the workspace's notes (charge_of their spans): one for each
  passage (a span of PASSAGE_KINDS, what the compiler makes a claim of: a paragraph, list
  item, quote, code block or table), and one for every full 40 bytes of the headings
  stored with each span, passages and heading spans alike; and
  `view.history_bytes()` the UTF-8 bytes of every page version it keeps, latest included
  (body and claims as stored). Both are running totals in the table account_disk, kept by
  triggers on spans and page_versions, so every write that adds or removes one (a note, an
  import, a page save, a dropped version, forgetting a note, deleting a project) counts it in
  its own transaction; `delete_account` removes the row. `view.record_model_use(tokens_in,
  tokens_out, micros)` adds a model call to the current month (`micros`: millionths of a
  US dollar); each is a whole number of at least 0 (ValueError otherwise, and nothing is
  written; KeyError for a view whose workspace is gone), recorded whatever the budget. A
  workspace's usage is its own: no other workspace reads it, and `delete_account` removes
  it. Reading usage writes nothing. A narrowed view reads none of it (`usage`,
  `stored_bytes`, `passages` and `history_bytes` are refused as a management call is), but
  its notes and model calls are metered for the workspace like any other.
- Plans (H6). `Account.plan` is the name of the server's plan the workspace is on, None
  until set. `set_plan(account_id, name_or_None)` (not scoped, like the account methods;
  KeyError for an unknown account; ValueError for a name that is not 1 to PLAN_NAME_MAX
  characters without surrounding blanks) records it. Which plans exist and what each
  allows is the server's configuration (cairn.plans), never the database's: any name is
  stored, and a name the server does not know reads as its default plan.
- Limits (H6; frozen by tests/acceptance/test_h6_quotas.py). `view.limited(Limits(...))` is
  a drop-in view over the same workspace, one more layer like `narrow`, that wraps whatever
  view it is given: `Limits(notes_per_month, storage_bytes, projects, model_budget_micros)`,
  each a whole number of at least 0 or None (no cap; ValueError otherwise; TypeError for
  anything but a Limits). Limiting a limited view keeps the tighter of each cap. A limited
  view is bound like a narrowed one: `for_account` returns it for its own workspace and
  refuses another (PermissionError), so it cannot shed its caps. Caps are
  the workspace's, whatever the view reaches. `view.limited(limits, resolve=f)` (H6 fix 2):
  `f` maps a plan's name (None: the default) to its Limits (cairn.plans.Plans.limits_of);
  each write the view admits then reads the workspace's plan in force inside its own
  transaction, by Store.plan_in_force's rule on the same connection (one function,
  store/usage.py `_plan_in_force`), asks `f`, and tightens its own limits with the
  answer, so a downgrade between admission and the write holds the write (and, with no
  static cap beside it, as cairn.plans.Plans.limit gives, an upgrade frees it). Its
  OverLimit carries `plan`, the name read, and `resolved` True. TypeError: `f` not
  callable. Before the guard screens a note, a limited view looks at the counters once (a
  read): a workspace already at a cap gets OverLimit without the note being screened, a
  note the project holds still passes as "unchanged", and the check inside the write stays
  the authority. Inside the
  write's own transaction (BEGIN IMMEDIATE, so exact across threads and processes), after
  reach, the guard, the name check and the duplicate check: `add_note` raises
  `OverLimit` when the month's notes are at the cap or the note's UTF-8 size would take
  `stored_bytes` past the storage cap, or its charge (charge_of its spans) would take
  `passages()` past storage_bytes // BYTES_PER_PASSAGE (OverLimit "passages", `used`
  what `passages()` holds); `create_project` and `create_projects` (the whole
  submission, counting only names not yet taken) when the projects would pass the cap;
  `import_export` when the archive's projects, its notes (counted in the month of the
  import), their bytes or their passages would, and nothing is imported. `OverLimit(limit,
  used, cap, resets_at)`: `limit` one of LIMITS or "passages", `resets_at` the first
  instant of the next UTC month for the month's notes, else None. A refusal writes nothing,
  not even an audit event.
  `Limits(versions_per_page=N)` (at least 1; not in LIMITS, since nothing meters it) is a
  bound, never a refusal: `save_page` through such a view drops, in its own transaction,
  the page's versions older than its last N, never the latest, and `import_export` does
  the same for each page it brings; a page's next version is its highest plus one, so
  numbers never start again. A view without it keeps every version; an export carries the
  versions that exist. The storage cap S bounds the history too, beside it, never a
  refusal: after `save_page` or `import_export` through a view with S, when the
  workspace's versions take more than HISTORY_FACTOR (4) * S bytes (`history_bytes()`,
  each version's body and claims as stored), its oldest versions by when they were saved,
  across its projects, go in the same transaction until they fit or only each page's latest
  is left (that one is never dropped).
  Without plans (Q1; cairn.store.history): `bound_history(N)` makes every save on any view
  of the connection keep its page's newest N versions in the same way (a view whose caps a
  plan resolves keeps its plan's instead; a view with its own versions_per_page keeps the
  lower), None every version, the default. `record_history_bound` keeps, in a row of the
  default workspace's settings no setting call reads, what a server runs with (a number,
  every version, or each plan's versions_per_page and the default plan), and
  `prune_by_record()` removes in one transaction what the last record says no page keeps
  (Pruned: how many; nothing without a record; workspaces on a plan it does not name are
  left alone and named). `pages_over(N)` counts the pages that hold more than N versions.
  Page heads (Q1, schema step 26): page_heads holds one row per page, its latest version's
  number, title, claim count and time, kept by two triggers on page_versions (a version
  saved, a head's version removed); `page_heads()` and `page_counts()` read it, so listing a
  project's pages reads no version's body. Deleting a project or a workspace removes its heads
  first; step 26 fills the table from the latest version of every page.
  What the storage cap bounds, and what it does not (H6c). `storage_bytes` (S) counts
  note text, the UTF-8 bytes of each note held. Beside it, with N notes held:
  * a second copy of the text, in the notes' spans: about S;
  * each note's fields (FIELD_MAX; the title is stored cut to 80 characters): at most
    80 + 200 + 80 + 500 + 40 = 900 characters, 3,600 bytes in UTF-8, so at most 3,600 N
    bytes; N grows by at most notes_per_month a month (every new note counts, whatever its
    size), and nothing else bounds it;
  * passages: `passages()` is at most S // BYTES_PER_PASSAGE (40) (OverLimit "passages").
    It counts charge_of every span held: one for each passage (a span that becomes a
    claim, PASSAGE_KINDS) and one for every full 40 bytes of each span's heading path as
    stored (path_bytes), passages and heading spans alike. A passage under a 390-byte
    path costs 10, a heading span under one 9; under headings shorter than 40 bytes in
    all, a passage costs 1 and a heading nothing. What is built of each passage (its span,
    claim and search rows) costs about the same whatever its length (about 380 bytes,
    measured before H6c), so this is what bounds a workspace of tiny passages, and the
    charge pays for the path stored with each span;
  * pages: the versions kept take at most HISTORY_FACTOR (4) * S bytes of body and claims
    (`history_bytes()`), beyond each page's latest, which the budget never drops, and a
    plan's versions_per_page keeps fewer; a page holds at most PAGE_CLAIMS_MAX (2,000)
    claims, which bounds what each save rewrites;
  * the search index holds each page's latest claims, plus what FTS5 keeps of replaced
    rows until it merges them.
  Measured (H6c, through cairn.memory.Memory.note on a view with S = 400 kB and notes of
  3 kB until a cap refused one, database after compact()): 11.4 to 12.9 times S for
  one-word list items or prose lines, on one page (a new topic each time the page was
  full) or a page per note, with every version kept, 20 or 1; the frozen test
  tests/acceptance/test_h6c_disk.py holds five shapes under 30 times S. A heading is
  stored with every span under it (its heading path), so the path is bounded too
  (HEADINGS_MAX, 400 bytes): at S = 60 kB, list items under one 390-byte heading, six
  nested 60-byte headings (plain or accented) or a 390-byte heading per passage take 5.7
  to 28.3 times S (tests/unit/test_heading_bound_unit.py holds them under 30), where a
  20 kB heading had taken 354 times. Heading paths are charged against the passages cap
  and a note's headings may outnumber its passages by at most 8, so the shape that had
  measured 57.7 times S (a 390-byte heading over 2,000 short "##" headings per note) is
  refused; with those bounds, twelve shapes at S = 60 kB through REST, the five frozen
  ones among them, take at most 16 times S after compact()
  (tests/unit/test_heading_bound_unit.py). What a note makes whatever its length (its
  note row, its audit event, and up to eight heading spans when it has no passage) is
  a fixed cost per note, which the caps on bytes and passages do not bound: notes of eight
  one-letter headings took 41 times S. So the bound has two terms: a workspace takes at
  most 30 times S plus 1,500 bytes for each note it holds, after compact(). The first term
  is what the caps on bytes and passages bound; the second grows only as notes are added,
  and a plan's notes_per_month is what bounds how fast. tests/unit/test_heading_bound_unit.py
  (test_a_workspace_takes_thirty_times_its_cap_and_a_bound_per_note) fills workspaces
  through REST to a cap with fifteen shapes, the five frozen ones, the long-heading ones and
  the smallest notes there are (one heading; eight one-letter headings, alone and after
  dense list items) among them, and holds each to both terms; the most any took is 0.74 of
  the bound. tests/acceptance/test_h6c_disk.py states the first term only.
  Not changed: a clock that steps back across a UTC month
  boundary meters the writes it admits in the earlier month (each write is counted in the
  month of the instant it is stored at).
  `view.model_budget_left()`: micros left this month, None without a budget; a read.
  `record_model_use` is never refused. A caller about to call a model reserves what the call
  may cost instead (Reservations, below), which is refused when it does not fit.
  NO_LIMITS caps nothing; every view starts with it.
- Reservations (T4b; store/reservations.py). `view.reserve_model_use(micros) -> id | None`
  takes `micros` from what is left of the month's model budget in one BEGIN IMMEDIATE
  transaction, booked into the month's `model_micros` at once: None, writing nothing, when
  nothing is left or it does not fit; always granted without a budget. The budget is the
  view's caps for that write (`_caps`): with a resolver, the plan in force read in it.
  With a `project_id` (M4), KeptFromModels, writing nothing, when that project is at
  "never", read first in the same transaction, with or without a budget.
  `view.settle_model_use(id, tokens_in, tokens_out, micros)` replaces it with what was
  spent, in the month it was booked in, recorded whatever the budget (KeyError: no open
  reservation of the workspace by that id; ValueError: a count that is not a whole number
  of at least 0). A reservation never settled counts as spent in full;
  `view.model_reservations()` lists the open ones (Reservation: id, period, micros,
  reserved_at), refused on a narrowed view as `usage` is; reserving and settling are not.
  `delete_account` deletes them.
- The plan in force. `plan_in_force(account_id) -> str | None` (not scoped, like
  get_account; KeyError for an unknown workspace) is one read by the workspace's id that
  writes nothing: the plan the operator set (`set_plan`, `Account.plan`), None for the
  server's default. Nothing else decides it. `list_account_summaries() ->
  list[AccountSummary]` (not scoped, a read; PermissionError for a view acting for a
  person, and refused on a narrowed view as management) is every workspace in creation
  order with its name, created_at, how many people belong to it and its plan in force, in
  one read for them all: never an address. The tables billing wrote (subscriptions,
  billing_messages, checkouts; schema steps 12, 13 and 27) stay in the database; nothing
  reads or writes them, except that `delete_account` deletes a workspace's rows in them, and an
  export leaves them out.
- Ids: accounts "acct_" + 12 hex chars, projects "br_" + 12 hex chars, notes "cap_" + 12
  hex chars, sets "set_" + 12 hex chars. Timestamps are ISO 8601 UTC strings.
- Schema: `PRAGMA user_version` holds SCHEMA_VERSION (2 added accounts, 3 the OAuth
  tables, 4 people, 5 reach and search statistics, 6 settings, the audit log and the key a
  session was opened with, 7 actor ids, 8 project kinds, 9 sources, 10 invitations, project
  rules, makers and removal markers, 11 metering and plans, 12 subscriptions and the
  provider messages seen, 13 when a subscription started and became past due, 14
  model-use reservations, 15 the digests of forgotten notes, 16 each workspace's passages
  and page history, 17 the end of key sessions that cannot be tied to a live key, 18 the
  passages counter charging the headings stored with each span, 19 the MCP address each
  OAuth code and grant is for, 20 a person's third level on a project, "manage"). Opening an older
  database migrates it in place, one version at a time.
  From v0 (user_version 0, v0's tables): every project, key and session moves to the default
  account, v0's search index becomes the default account's, and nothing is lost. From 2: the
  OAuth tables are added, empty, and nothing that existed changes. From 3: users,
  identities and memberships are added, empty, and sessions, OAuth codes and grants gain an
  empty person column (the three tables are rebuilt with every row kept, so a migrated
  schema reads exactly like a fresh one). From 4: project sets are added, empty, keys,
  codes and grants gain a reach, every project to write, as they had before (the three tables
  are rebuilt the same way), and the search statistics are added and counted from each
  account's index as it stands. From 5: settings and the audit log are added, empty, and
  sessions gain an empty key label (the table is rebuilt the same way). From 6: each key
  gets an id of its own, and sessions and audit events gain an empty key id and actor id
  (the three tables are rebuilt the same way). From 7: every project gains a kind, "general"
  (the table is rebuilt the same way). From 8: sources are added, empty, and nothing that
  existed changes. From 9: invitations, project rules and removal markers are added, empty,
  every project gains
  no maker and `everyone` "write", and every key no maker, so every project stays open to
  every member and every key the workspace's (the two tables are rebuilt the same way).
  From 10: usage by month and each workspace's plan and stored bytes are added beside the
  accounts (accounts itself is not rebuilt); every workspace is on the default plan, and
  the notes it holds are counted, their UTF-8 size in its total and each in the UTC month
  of its `created_at`. From 11: subscriptions and the provider messages seen are added,
  empty; every workspace stays on the plan the operator set. From 12 (schema.py's
  _SUBSCRIPTION_TIMES step): each subscription gains when it started and when it became
  past due, both unknown for the rows that exist. From 13: model-use reservations are
  added, empty, and nothing that existed changes. From 14 (schema.py's _FORGOTTEN step):
  the digests of forgotten notes are added, empty, and nothing that existed changes; they
  are never exported. From 15 (schema.py's _DISK_BOUNDS step, 16): account_disk is added
  with each workspace's passages and page history counted from its rows, and the triggers
  that keep both; nothing that existed changes. From 16 (schema.py's _KEY_SESSIONS step,
  17): no table changes; every session opened with a key (it has a label) that cannot be
  tied to a key that still exists ends: one recorded before sessions kept the key's id, and
  one whose key was revoked before, which outlived it. Its browser signs in again. From
  17 (schema.py's _HEADING_CHARGE step, 18): each workspace's `passages()` is counted
  again from its rows with the headings stored with each span charged, and the triggers
  count so from then on; a workspace's count rises and can pass its plan's cap, and then
  its new notes are refused until some are removed or the plan changes. From 18
  (schema.py's _GRANT_AUDIENCE step, 19): OAuth codes and grants gain an audience, and
  every grant, code and token that exists ends, since none recorded the address it was
  approved at (the step's docstring says why); client registrations are kept, and an agent
  connects again with a person's approval. From 19 (schema.py's _MANAGE_LEVEL step, 20):
  project_people is rebuilt so that a person's level may be "manage" (SQLite cannot change a
  CHECK in place), with every row it held kept at its level and its index made again.
  From 20 (schema.py's _SOURCE_SWITCH step, 21; S2): sources are rebuilt with `enabled`
  and `enabled_at`, and every source that exists is on, switched on at its `created_at`
  (it read from the moment it was added); every other column is kept. From 21
  (schema.py's _VOUCHED_EMAIL step, 22; B1): sessions are rebuilt with `vouched_email`,
  every session that exists kept as it was with none, so it is found by its person's
  stored address only. From 24 (schema.py's _WRITERS step, 25; A1): note_writers,
  made_with and recorded_since are added, the first two empty, so every note that exists
  has no recorded writer ("unknown") and every key and grant records no key it was made
  with; recorded_since holds the moment the step ran, read from the store's clock (a new
  database holds the moment it was made).
  Each version step
  is one transaction, so a step that fails leaves the database as it was.
  Opening a database whose version is higher than SCHEMA_VERSION raises SchemaTooNew and
  leaves the file untouched. A file that is neither empty nor a v0 database (v0's tables,
  each with exactly v0's columns) is refused the same way, with sqlite3.DatabaseError.
  Opening one at SCHEMA_VERSION that lacks a table of the current schema raises
  UnknownSchema ("the database was not made by this version of cairn: it has schema version
  N but no table T", the first such table) before anything is written, and leaves the file
  untouched.
  A path SQLite may not open, create or write while the store opens, or a file that is not a
  SQLite database at all, raises CannotOpen (a sqlite3.DatabaseError, from SQLite's own
  error) before anything is written; a busy database raises SQLite's error as it is.
"""

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from cairn.store.access import (
    ADDRESS_MAX,
    LEVELS,
    MODELS,
    PERSON_ACCESS,
    READ_ONLY_PROJECT,
    SHARE_MAX,
    AccessMethods,
    KeptFromModels,
)
from cairn.store.accounts import ACCOUNT_NAME_MAX, AccountMethods
from cairn.store.audit import (
    ACTOR_KINDS,
    AUDIT_LIMIT_MAX,
    DEFAULT_GUARD_POLICY,
    GUARD_POLICIES,
    NOTE_OUTCOMES,
    POLICY_KINDS,
    POLICY_SETTING,
    SCREENED_FIELDS,
    SETTINGS,
    AuditMethods,
)
from cairn.store.base import (
    ACCOUNT_ID,
    AUDIT_KEEP,
    AUDIT_PRUNE_BATCH,
    CANNOT_WRITE,
    DEFAULT_ACCOUNT_ID,
    UNUSABLE,
    CannotWrite,
    ConnectionState,
    Readers,
    StoreUnusable,
    _transaction,
)
from cairn.store.credentials import KEY_PREFIX, CredentialMethods
from cairn.store.erasure import ErasureMethods
from cairn.store.expiry import ExpiryMethods
from cairn.store.grants import (
    ACCESS_TOKEN_PREFIX,
    ACCESS_TOKEN_TTL,
    CLIENT_NAME_MAX,
    CODE_TTL,
    REDIRECT_URI_MAX,
    REDIRECT_URIS_MAX,
    REFRESH_TOKEN_PREFIX,
    REFRESH_TOKEN_TTL,
    UNUSED_CLIENT_TTL,
    GrantMethods,
)
from cairn.store.history import HistoryMethods, Pruned
from cairn.store.hosted import HostedMethods
from cairn.store.memory import (
    DESCRIPTION_MAX,
    FACTS_MAX,
    FIELD_MAX,
    HEADINGS_MAX,
    HEADINGS_OVER_PASSAGES,
    PAGE_CHARS_MAX,
    PAGE_CLAIMS_MAX,
    PROJECT_NAME,
    TITLE_MAX,
    CitesForgotten,
    DescriptionTooLong,
    FieldTooLong,
    FiledElsewhere,
    ForgottenNote,
    HeadingsTooLong,
    MemoryMethods,
    NameTaken,
    NoteRefused,
    PageTooLong,
    ProjectTaken,
    StalePage,
    TooManyFacts,
    TooManyHeadings,
    check_facts,
    claim_chars,
    count_facts,
    is_project_name,
    note_key,
    note_title,
    stored_key,
)
from cairn.store.models import (
    Access,
    Account,
    AccountSummary,
    ActorKind,
    AuditEvent,
    Claim,
    CodeInfo,
    CompactionState,
    CredentialInfo,
    ExportSummary,
    Forgotten,
    Found,
    GrantInfo,
    Invite,
    InviteInfo,
    KeyInfo,
    Made,
    Member,
    Note,
    OAuthClient,
    OpenInvite,
    Page,
    PageHead,
    Principal,
    Project,
    ProjectAccess,
    ProjectSet,
    ProjectSharing,
    Searched,
    SearchHit,
    Shared,
    SignedIn,
    SourceClaim,
    SourceInfo,
    TokenPair,
    Usage,
    User,
    WaitingNote,
    Workspace,
)
from cairn.store.numbers import NumbersMethods
from cairn.store.people import (
    INVITE_TTL,
    JOINS,
    ROLES,
    USER_NAME_MAX,
    PeopleMethods,
    ProviderChanged,
    Removed,
)
from cairn.store.portable import CompactIncomplete, PortableMethods
from cairn.store.reach import ACCESS, LIMITED, READ_ONLY, ReachMethods
from cairn.store.reservations import Reservation, ReservationMethods
from cairn.store.schema import (
    SCHEMA_VERSION,
    CannotOpen,
    SchemaTooNew,
    UnknownSchema,
    _refuse,
    _upgrade,
)
from cairn.store.sets import SET_ID_PREFIX, SetMethods
from cairn.store.slack import SlackMethods
from cairn.store.sources import SOURCE_CLAIM_TTL, SourceMethods, SourceOff
from cairn.store.usage import (
    BYTES_PER_PASSAGE,
    HISTORY_FACTOR,
    LIMITS,
    NO_LIMITS,
    PASSAGE_KINDS,
    PERIOD,
    PLAN_NAME_MAX,
    Limits,
    OverLimit,
    UsageMethods,
    charge_of,
    path_bytes,
)

# Store.health: how long it waits for the store's lock, and for SQLite's, before it answers.
# Docker's probe and the hosted updater's give up after 3 seconds; this answers within 2.
HEALTH_WAIT = 1.0
Health = Literal["ok", "busy", "unavailable"]
_HELD = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)  # another connection holds the file

__all__ = [
    "ACCESS",
    "ACCESS_TOKEN_PREFIX",
    "ACCESS_TOKEN_TTL",
    "ACCOUNT_ID",
    "ACCOUNT_NAME_MAX",
    "ACTOR_KINDS",
    "ADDRESS_MAX",
    "AUDIT_KEEP",
    "AUDIT_LIMIT_MAX",
    "AUDIT_PRUNE_BATCH",
    "BYTES_PER_PASSAGE",
    "CANNOT_WRITE",
    "CLIENT_NAME_MAX",
    "CODE_TTL",
    "DEFAULT_ACCOUNT_ID",
    "DEFAULT_GUARD_POLICY",
    "DESCRIPTION_MAX",
    "FACTS_MAX",
    "FIELD_MAX",
    "GUARD_POLICIES",
    "HEADINGS_MAX",
    "HEADINGS_OVER_PASSAGES",
    "HEALTH_WAIT",
    "HISTORY_FACTOR",
    "INVITE_TTL",
    "JOINS",
    "KEY_PREFIX",
    "LEVELS",
    "LIMITED",
    "LIMITS",
    "MODELS",
    "NOTE_OUTCOMES",
    "NO_LIMITS",
    "PAGE_CHARS_MAX",
    "PAGE_CLAIMS_MAX",
    "PASSAGE_KINDS",
    "PERIOD",
    "PERSON_ACCESS",
    "PLAN_NAME_MAX",
    "POLICY_KINDS",
    "POLICY_SETTING",
    "PROJECT_NAME",
    "READ_ONLY",
    "READ_ONLY_PROJECT",
    "REDIRECT_URIS_MAX",
    "REDIRECT_URI_MAX",
    "REFRESH_TOKEN_PREFIX",
    "REFRESH_TOKEN_TTL",
    "ROLES",
    "SCHEMA_VERSION",
    "SCREENED_FIELDS",
    "SETTINGS",
    "SET_ID_PREFIX",
    "SHARE_MAX",
    "SOURCE_CLAIM_TTL",
    "TITLE_MAX",
    "UNUSABLE",
    "UNUSED_CLIENT_TTL",
    "USER_NAME_MAX",
    "Access",
    "Account",
    "AccountSummary",
    "ActorKind",
    "AuditEvent",
    "CannotOpen",
    "CannotWrite",
    "CitesForgotten",
    "Claim",
    "CodeInfo",
    "CompactIncomplete",
    "CompactionState",
    "CredentialInfo",
    "DescriptionTooLong",
    "ExportSummary",
    "FieldTooLong",
    "FiledElsewhere",
    "Forgotten",
    "ForgottenNote",
    "Found",
    "GrantInfo",
    "HeadingsTooLong",
    "Health",
    "Invite",
    "InviteInfo",
    "KeptFromModels",
    "KeyInfo",
    "Limits",
    "Made",
    "Member",
    "NameTaken",
    "Note",
    "NoteRefused",
    "OAuthClient",
    "OpenInvite",
    "OverLimit",
    "Page",
    "PageHead",
    "PageTooLong",
    "Principal",
    "Project",
    "ProjectAccess",
    "ProjectSet",
    "ProjectSharing",
    "ProjectTaken",
    "ProviderChanged",
    "Pruned",
    "Removed",
    "Reservation",
    "SchemaTooNew",
    "SearchHit",
    "Searched",
    "Shared",
    "SignedIn",
    "SourceClaim",
    "SourceInfo",
    "SourceOff",
    "StalePage",
    "Store",
    "StoreUnusable",
    "TokenPair",
    "TooManyFacts",
    "TooManyHeadings",
    "UnknownSchema",
    "Usage",
    "User",
    "WaitingNote",
    "Workspace",
    "charge_of",
    "check_facts",
    "claim_chars",
    "count_facts",
    "is_project_name",
    "note_key",
    "note_title",
    "path_bytes",
    "stored_key",
]


# Store is assembled from one class per area (accounts.py, audit.py,
# credentials.py, erasure.py, expiry.py, grants.py, history.py, memory.py, numbers.py,
# people.py, portable.py, sets.py, slack.py, sources.py, usage.py), each built
# on base.StoreBase: the connection, the lock, the clock, the account binding and the actor.
# ReachMethods (reach.py) comes first, so that a narrowed view sees each call before the
# area that serves it. Opening the database lives here because it runs the schema upgrade
# (schema.py), which itself builds on base.py.
class Store(
    ReachMethods,
    AccessMethods,
    AccountMethods,
    AuditMethods,
    CredentialMethods,
    GrantMethods,
    MemoryMethods,
    ErasureMethods,
    ExpiryMethods,
    PeopleMethods,
    PortableMethods,
    SetMethods,
    SourceMethods,
    UsageMethods,
    HistoryMethods,
    NumbersMethods,
    ReservationMethods,
    HostedMethods,
    SlackMethods,
):
    """One account's view of the SQLite database that holds every account."""

    def __init__(self, path: str | Path, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = threading.Lock()
        self._state = ConnectionState()  # shared with every view, as the lock is
        self._owner = True  # of the connection: views share it and never close it
        self._clock_fn = clock  # every view made from this store shares it
        self._bind(DEFAULT_ACCOUNT_ID)
        # Autocommit mode: every write opens its own BEGIN IMMEDIATE transaction (_write).
        try:
            self._con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        except sqlite3.Error as error:
            _refuse(path, error)  # CannotOpen when SQLite may not open or create the file
            raise
        try:
            # Freed rows and pages are zeroed: what makes a deletion an erasure (see
            # delete_account in the contract). A library that cannot is refused, not trusted.
            self._con.execute("PRAGMA secure_delete = ON")
            if self._con.execute("PRAGMA secure_delete").fetchone() != (1,):
                raise RuntimeError("this SQLite does not zero what it deletes (secure_delete)")
            _upgrade(self._con, self._stamp)
            if str(path) != ":memory:":
                (journal,) = self._con.execute("PRAGMA journal_mode = WAL").fetchone()
                self._state.readers = _readers_for(self._con, journal)
        except sqlite3.Error as error:
            self._con.close()
            _refuse(path, error)  # CannotOpen when the file is no database or may not be written
            raise
        except BaseException:
            self._con.close()
            raise

    def health(self, wait: float = HEALTH_WAIT) -> Health:
        """Whether the store can serve, from one bounded read that writes nothing (what
        /healthz answers, through cairn.web.auth.Accounts):

        - "ok": the read worked and the file holds the schema this code knows
          (`PRAGMA user_version` is SCHEMA_VERSION, and the default account's row reads).
        - "busy": the store is alive and held on purpose. Either this process holds the
          store's lock for longer than `wait` seconds (a write holds it; compact holds it
          for a whole rewrite of the file, and every write waits behind it), or another
          connection holds the file past `wait` (SQLite's busy wait is held to `wait` for
          this read, then put back). Each wait is bounded, so the call returns within about
          twice `wait`.
        - "unavailable": the store is closed, unusable (base.StoreUnusable), the read
          failed with any other error, or the file holds another schema version.

        Busy is told from broken by who answers: a lock that is held means some other call
        is at work on a connection that worked when it began; a broken connection is
        reported only by a read that ran and failed, or by the store's own record that it
        is unusable, which is read without the lock. A call that held the lock forever
        would read as busy: the store cannot tell a long call from a stuck one."""
        if self._state.unusable:
            return "unavailable"
        if not self._lock.acquire(timeout=wait):
            return "busy"
        try:
            if self._state.unusable:
                return "unavailable"
            try:
                return self._health_read(wait)
            except StoreUnusable:
                self._unusable()
                return "unavailable"
            except sqlite3.Error as error:
                code = getattr(error, "sqlite_errorcode", None)
                busy = code is not None and code & 0xFF in _HELD
                return "busy" if busy else "unavailable"
        finally:
            self._lock.release()

    def _health_read(self, wait: float) -> Health:
        """Under the lock: the read of `health`, with SQLite's busy wait held to `wait`."""
        con = self._con
        (waited,) = con.execute("PRAGMA busy_timeout").fetchone()
        con.execute(f"PRAGMA busy_timeout = {max(1, int(wait * 1000))}")
        try:
            with _transaction(con, "DEFERRED"):
                (version,) = con.execute("PRAGMA user_version").fetchone()
                found = con.execute(
                    "SELECT 1 FROM accounts WHERE id = ?", (DEFAULT_ACCOUNT_ID,)
                ).fetchone()
        finally:
            con.execute(f"PRAGMA busy_timeout = {int(waited)}")
        return "ok" if version == SCHEMA_VERSION and found else "unavailable"


def _readers_for(con: sqlite3.Connection, journal: str) -> Readers | None:
    """The pool of read-only connections for the database `con` opened, asked of SQLite: a
    file in WAL has one; a temporary database (no file a second connection could open) or one
    SQLite keeps out of WAL has none, and its reads stay on the writer (StoreBase._read)."""
    file = next(row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main")
    if journal.lower() != "wal" or not file:
        return None
    return Readers(Path(file).as_uri() + "?mode=ro")
