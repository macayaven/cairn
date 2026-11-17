"""The records the store returns: pydantic models, frozen."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# What a connection (an API key or an agent's grant) may do in the projects it reaches.
Access = Literal["write", "read"]
# What a person may do in a project (T1): a project's rule gives "none" to whoever it leaves out.
Level = Literal["write", "read", "none"]
# What a project's rule can give one person (T2): "manage" is "write" and deciding who reaches
# that project. Never `everyone`'s, and not a connection's (Access).
PersonAccess = Literal["manage", "write", "read"]
Role = Literal["owner", "admin", "member"]


class Account(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    created_at: str
    plan: str | None = None  # the name of the server's plan it is on; None: the default (H6)


class Usage(BaseModel):
    """What a workspace used in one UTC month (H6): the notes it stored and their UTF-8
    size, and the model calls recorded for it, in tokens and in millionths of a US dollar."""

    model_config = ConfigDict(frozen=True)

    period: str  # "YYYY-MM"
    notes: int = 0
    note_bytes: int = 0
    model_tokens_in: int = 0
    model_tokens_out: int = 0
    model_micros: int = 0


class KeyInfo(BaseModel):
    """What may be shown of an API key: never the key, never its digest."""

    model_config = ConfigDict(frozen=True)

    label: str
    created_at: str
    projects: tuple[str, ...] | None = (
        None  # the project and set ids it reaches; None: every project
    )
    access: Access = "write"
    maker_id: str | None = None  # the person who made it; None: the workspace's own key
    # A1: the label of the key it was made with, from a web session opened with that key;
    # None when it was made otherwise, or when that key is revoked (then made_with_revoked).
    made_with: str | None = None
    made_with_revoked: bool = False
    # A1: the moment recording what keys made began, when this key was made before it.
    unrecorded_before: str | None = None


class Principal(BaseModel):
    """Who a key, a session token or an OAuth access token belongs to."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    kind: Literal["key", "session", "token"]
    # The key's label or the token's client name; for a session, the label of the key it was
    # opened with, and None for one a person signed in to (or one made by a bare view).
    label: str | None = None
    # The person the credential acts for: who signed in to a session (or made the key it was
    # opened with), who approved a token's agent, who made a key; None: the workspace's own.
    user_id: str | None = None
    # The connection's reach: the project and set ids it may use (None: every project of the
    # workspace, now and later), and whether it may write there. A session has every project.
    projects: tuple[str, ...] | None = None
    access: Access = "write"


class User(BaseModel):
    """A person who signs in through an identity provider."""

    model_config = ConfigDict(frozen=True)

    id: str
    email: str  # verified by the provider the person first signed in with
    name: str
    created_at: str


class Member(BaseModel):
    """A person's place in a workspace (account)."""

    model_config = ConfigDict(frozen=True)

    user_id: str
    email: str
    name: str
    role: Role


class Workspace(BaseModel):
    """One of a person's workspaces, with their role there."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    name: str
    role: Role


class Invite(BaseModel):
    """An invitation as it is made: its token in plaintext for the only time."""

    model_config = ConfigDict(frozen=True)

    id: str
    email: str
    role: Role
    created_at: str
    expires_at: str
    token: str = Field(repr=False)


class OpenInvite(BaseModel):
    """An invitation still waiting, as a workspace's managers see it: never its token."""

    model_config = ConfigDict(frozen=True)

    id: str
    email: str
    role: Role
    created_at: str
    expires_at: str


class InviteInfo(BaseModel):
    """What the holder of an open invitation's token may learn: where it leads, and who it
    is for."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    workspace: str  # the workspace's name
    email: str
    role: Role


class SignedIn(BaseModel):
    """What a sign-in made: a session token, in plaintext for the only time, for `user` in
    the workspace `account_id`. `created`: this sign-in made the user. `invited`: it took
    an open invitation to get them in (so the invitation's link is used up)."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(repr=False)
    user: User
    account_id: str
    created: bool
    invited: bool = False


class OAuthClient(BaseModel):
    """A registered OAuth client. It is public and belongs to no account: it has no secret,
    and its name is whatever it called itself."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    redirect_uris: tuple[str, ...]
    created_at: str


class GrantInfo(BaseModel):
    """What may be shown of a grant, one client's access to one account: never a token,
    never a digest."""

    model_config = ConfigDict(frozen=True)

    id: str
    client_id: str
    client_name: str
    created_at: str
    projects: tuple[str, ...] | None = (
        None  # the project and set ids it reaches; None: every project
    )
    access: Access = "write"
    # A1: the label of the key its approval was made with, as KeyInfo's.
    made_with: str | None = None
    made_with_revoked: bool = False


class Made(BaseModel):
    """What the keys of one label made (A1, Store.made_by): the live keys and agents made
    with them, or with a key they made, at any depth, oldest first, by label and by name; and
    the moment recording began (`recorded_since`) when one of those keys was made before it,
    so what it made then was not recorded. Never a key, a token or a digest."""

    model_config = ConfigDict(frozen=True)

    keys: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    unrecorded_before: str | None = None

    def __bool__(self) -> bool:
        """Whether the keys made anything still live."""
        return bool(self.keys or self.agents)


class CodeInfo(BaseModel):
    """What the token endpoint checks an authorization code against before redeeming it."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    redirect_uri: str
    redirect_uri_explicit: bool  # whether the authorization request named the redirect URI
    code_challenge: str
    expires_at: datetime


class TokenPair(BaseModel):
    """A new access token and refresh token, in plaintext for the only time."""

    model_config = ConfigDict(frozen=True)

    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_in: int  # seconds the access token lasts


class ProjectSet(BaseModel):
    """A named group of projects of one workspace. A reach that names the set covers whatever it
    holds when a request is served."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    project_ids: tuple[str, ...]  # in the order they were given
    created_at: str


# Who a view acts for, as the audit log names them.
ActorKind = Literal["key", "token", "session", "system"]


class AuditEvent(BaseModel):
    """One write or security event of a workspace. Never note text, a secret or a digest:
    `detail` holds names, ids and counts only."""

    model_config = ConfigDict(frozen=True)

    at: str
    actor_kind: ActorKind
    actor: str | None  # the key's label, the agent's client name or the person; None: system
    # The key's id, the agent's grant id or the person's id (labels and client names repeat;
    # a session opened with a key: that key's id); None for system and for events recorded
    # before schema 7.
    actor_id: str | None = None
    action: str  # "note.saved", "key.created", ...
    project: str | None  # the project's name when the event is about one
    detail: dict[str, Any] = Field(default_factory=dict)


class Project(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str = ""
    created_at: str
    kind: str = "general"  # an id of cairn.kinds.KINDS: what the project is for
    maker_id: str | None = None  # the person whose view made it; None: a key or the operator


class ProjectAccess(BaseModel):
    """Who reaches a project besides the workspace's owners, who reach every project: its maker,
    who writes; `people`, user ids with "read", "write" or "manage" (write, and decide who
    reaches it); and `everyone`, what any other member gets."""

    model_config = ConfigDict(frozen=True)

    maker_id: str | None
    everyone: Level
    people: dict[str, PersonAccess]


class Shared(BaseModel):
    """What sharing a project with a list of addresses did (Store.share_project): `granted`, the
    user ids now at the level given, in the order typed, each once; `unknown`, the addresses
    that are no member's, as typed less outer spaces, each once."""

    model_config = ConfigDict(frozen=True)

    granted: tuple[str, ...]
    unknown: tuple[str, ...]


class ProjectSharing(BaseModel):
    """How far a project is shared, as anyone who reaches it may read: what any member gets
    (`everyone`), and the people its rule names (`people`: its maker and those the rule
    lists, by user id). Owners reach every project and are not counted unless named."""

    model_config = ConfigDict(frozen=True)

    everyone: Level
    people: frozenset[str]


class Note(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    project_id: str
    text: str
    title: str
    topic: str | None = None
    label: str | None = None
    source_ref: str | None = None
    via: str | None = None
    created_at: str
    # N1: the topic it is filed under (cairn.topics.topic_key), made once when it was stored,
    # from its topic, else its title as given. None only for a note made outside the
    # store: the key of its topic, else its title, then (cairn.store.stored_key).
    topic_key: str | None = None
    # N1: stored before notes were filed by topic (schema step 24, or an archive written
    # before): sent again, the same note whatever topic or title it comes with.
    before_topics: bool = False


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    span_ids: tuple[str, ...]

    def shown(self) -> dict[str, Any]:
        """The claim as callers meet it (a page's answer, an export): the spans it cites are
        its `passage_ids`. What is stored keeps `span_ids`."""
        return {"id": self.id, "text": self.text, "passage_ids": list(self.span_ids)}


# The names of a version's metrics that callers meet (a page's answer, an export) where the
# stored name differs. What is stored keeps its names.
SHOWN_METRICS = {"spans_cited": "passages_cited", "span_coverage": "passage_coverage"}


def shown_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """A version's stored metrics under the names callers meet (SHOWN_METRICS)."""
    return {SHOWN_METRICS.get(name, name): value for name, value in metrics.items()}


def stored_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Metrics under the names callers meet (an archive's), under their stored names."""
    stored = {shown: name for name, shown in SHOWN_METRICS.items()}
    return {stored.get(name, name): value for name, value in metrics.items()}


class Page(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    slug: str
    title: str
    body: str
    version: int
    claims: tuple[Claim, ...]
    metrics: dict[str, Any] = Field(default_factory=dict)  # the gate's metrics for this version
    updated_at: str


class PageHead(BaseModel):
    """A page as a list of pages shows it (Q1): its latest version's slug, title, number,
    claim count and time, without its body or its claims."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    version: int
    claims: int
    updated_at: str


class SearchHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    project: str  # the project's name
    slug: str
    title: str
    claim_id: str
    text: str  # the claim text
    span_ids: tuple[str, ...]
    score: float  # higher is better


class Searched(BaseModel):
    """What a search left out (Q2), worked out once (cairn.store.ranking.found); the doors
    word it from these facts alone (cairn.memory.searched)."""

    model_config = ConfigDict(frozen=True)

    words_searched: int  # the query's words that counted, a word given twice counted twice
    words_left_out: tuple[str, ...]  # the first ranking.WORDS_SHOWN of the others, as given
    words_left_out_count: int  # how many words were left out in all
    # Claims that match in the projects searched; None when not counted: more than
    # claims_ranked, which is then the search's bound (ranking.scored_max).
    claims_matched: int | None
    # False when the count stopped at its bound (ranking.COUNTED_MAX): more than
    # claims_matched claims match.
    claims_matched_exact: bool = True
    claims_ranked: int  # the matching claims scored
    # How the claims ranked were chosen: "all" of them; the "newest", for a query of one
    # matching word over the cap; the "likeliest" to score highest, for several.
    ranked_by: Literal["all", "newest", "likeliest"]
    # The projects the search covered: whether searching one project would narrow it. Only
    # the advice reads it; no answer shows it (the caller knows what it asked to search).
    projects_searched: int

    @property
    def complete(self) -> bool:
        """Every word of the query searched, and every matching claim ranked."""
        return not self.words_left_out_count and self.ranked_by == "all"


class Found(BaseModel):
    """A search's hits, best first, and what it left out."""

    model_config = ConfigDict(frozen=True)

    hits: list[SearchHit]
    searched: Searched


class SourceInfo(BaseModel):
    """A workspace's source (cairn.sources) as stored: never a credential (its config names
    the environment variables that hold them)."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str  # a name of cairn.sources.KINDS
    config: dict[str, Any]  # as the kind checked it
    cursor: str | None  # where the next run starts: the source's own, opaque
    created_at: str
    last_run_at: str | None  # when the last run ended, whether it worked or not
    last_report: dict[str, Any] | None  # the counts of the last run that worked
    last_error: str | None  # why the last run failed; None once one works
    enabled: bool  # off: no run reads it (Store.enable_source, Store.disable_source)
    enabled_at: str | None  # when it was last switched on; None while it never was
    # It also writes into a project the viewer does not reach, which `config["projects"]` leaves
    # out (Store.list_sources): shown as one row, never by name.
    unreached: bool = False


class CredentialInfo(BaseModel):
    """An account at a service, registered once by the operator (Store.add_credential, R2):
    its kind, whose it is, and how many sources read through it. Never its settings: they
    name the server's environment."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: str  # a name of cairn.sources.KINDS
    owner: str | None  # the person's user id; None for the operator's (the workspace's)
    owner_name: str | None  # the person's name as the store holds it; None for the operator's
    account: str | None  # the account's address when the credential names it (`subject`)
    added_at: str
    sources: int  # the sources made from it


class SourceClaim(BaseModel):
    """A run's hold on a source (Store.claim_source): what it starts from, and the token
    that lets it store what it found."""

    model_config = ConfigDict(frozen=True)

    name: str
    token: str = Field(repr=False)
    kind: str
    config: dict[str, Any]
    cursor: str | None
    enabled_at: str | None  # the last switch-on: the kind reads nothing that ended before it
    # The projects its config names, casefolded, and the id each had when the run claimed the
    # source: the run writes only there, so a name that comes to mean another project during
    # the run gets nothing.
    project_ids: dict[str, str] = Field(default_factory=dict)


class ExportSummary(BaseModel):
    """What an export holds, or an import restored: projects, notes and pages (each
    page once, however many versions it has)."""

    model_config = ConfigDict(frozen=True)

    projects: int
    notes: int
    pages: int
    # An import's sentence on who reaches what it brought, then one on its Models choice (M4)
    notice: str | None = None
    fields_cut: int = 0  # an import's note fields cut to their bounds (store.FIELD_MAX)


class WaitingNote(BaseModel):
    """A note stored and not yet on its page (N1): its id, its workspace and project (ids and
    names), when it was stored (ISO 8601 UTC) and how many tries to file it ended with no
    outcome. Never its text or title."""

    model_config = ConfigDict(frozen=True)

    note_id: str
    account_id: str
    workspace: str
    project_id: str
    project: str
    stored_at: str
    tries: int


class Forgotten(BaseModel):
    """What forgetting one note did (H5b): its id, the slugs of the pages rebuilt without it
    and of the pages removed because nothing else held them up, each sorted."""

    model_config = ConfigDict(frozen=True)

    note_id: str
    rebuilt: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


class CompactionState(BaseModel):
    """When a workspace was first deleted since the last compaction (None: none since), and
    when the last compaction began (None: never). ISO 8601 UTC."""

    model_config = ConfigDict(frozen=True)

    deleted_at: str | None = None
    compacted_at: str | None = None


class AccountSummary(BaseModel):
    """One workspace as the operator lists them (Store.list_account_summaries): its id,
    name and creation stamp, how many people belong to it, the plan it is held to now (by
    the rule of Store.plan_in_force: the plan the operator set, None: the server's default).
    No person's address."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    created_at: str
    members: int
    plan: str | None
