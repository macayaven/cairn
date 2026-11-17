"""Help (/help): a search of the product's own guides, published as one release into one
documentation project (cairn.docs_publisher) and read with a read-only key of its own.

The app has /help, and its footer a Help link, only when a release is configured
(`help_routes`, `HelpLink`); without one /help answers as any unknown address does.

What one search costs (`Help`):
- The release is verified against the store at most once per VERIFY_EVERY per process: the
  artifact is read and checked, then the project, its pages, the passages their claims cite
  and those passages' notes are read through the reader key's own narrowed view of the store
  (what `identify_key` and `Accounts.memory` give that key). Counts come first, and every
  read after them is bounded by the artifact (`_verified`), so a project that holds more
  than the release costs a fixed number of rows to refuse. The verified passages are kept
  in memory until the next verification. A failed verification is remembered for
  RETRY_AFTER, during which a search checks the reader key and reads nothing else.
- Every search checks the reader key again (still a key, still read-only, still reaching
  exactly the one project: a fixed number of reads), then searches the kept passages.
- All of it runs in a worker thread, never on the event loop, and one verification runs at
  a time: a second search waits for it, then uses what it kept.
- The reads go through no route, so a plan's requests_per_minute does not count them and
  the access log does not record them.
"""

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from markupsafe import Markup
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from cairn.docs_publisher import MAX_FILE_BYTES
from cairn.guard import PatternGuard
from cairn.spans import chunk
from cairn.web import markdown
from cairn.web.brand import SOFTWARE, Brand, product_of
from cairn.web.public import OPERATORS_GUIDE
from cairn.web.templating import HELP, brand_of
from cairn.web.ui import _ui

REPOSITORY = "https://github.com/macayaven/cairn"
SCHEMA = "cairn-docs-release-v1"
GUIDES = ("connect", "patterns", "sources", "security", "run", "privacy")
CONTENT = {"paragraph", "bullet", "code", "table", "quote"}
VERIFY_EVERY = timedelta(minutes=10)  # how long a verified release is searched without a check
RETRY_AFTER = timedelta(seconds=60)  # how long a failed verification answers for itself
RESULTS = 8  # passages a search shows at most
WORDS = 8  # words of a search that count
# The software's identifiers, kept as they are under a brand as /docs keeps them (B3: the
# rule of tests/acceptance/test_b3_brand_inside.py): the module, an environment variable,
# a file name the software chooses, the container image, a token's prefix.
IDENTIFIERS = re.compile(
    r"python3? -m cairn(?:\.[a-z_]+)*\b"
    r"|(?<![A-Za-z0-9_])CAIRN_[A-Z0-9_]+"
    r"|(?<![A-Za-z0-9_-])cairn\.(?:db|env)\b"
    r"|\bghcr\.io/[\w.-]+/cairn(?::[\w.-]+)?"
    r"|(?<![A-Za-z0-9_-])cairn\.(?:at|rt)\."
)


def names_the_software(text: str) -> bool:
    """Whether `text` names the software outside one of IDENTIFIERS."""
    return SOFTWARE in IDENTIFIERS.sub("", text).casefold()


QUERY_MAX = 200  # characters of a query

logger = logging.getLogger(__name__)


class Unavailable(ValueError):
    """The helper cannot search now. `reason` is a fixed code, the only thing logged."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str = "release"):
    raise Unavailable(reason)


def _artifact(path, release):
    if not isinstance(release, str) or not re.fullmatch(r"[0-9a-f]{40}", release):
        _fail("release")
    if path is None:
        _fail("release")
    with Path(path).open("rb") as file:
        raw = file.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        _fail("release")
    data = json.loads(raw)
    project = {
        "name": f"cairn-docs-{release}",
        "description": f"cairn documentation release {release}; {SCHEMA}",
    }
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "repository", "release", "project", "notes"}
        or data["schema"] != SCHEMA
        or data["repository"] != REPOSITORY
        or data["release"] != release
        or data["project"] != project
        or not isinstance(data["notes"], list)
        or not 6 <= len(data["notes"]) <= 128
    ):
        _fail("release")
    entries = {}
    index = 0
    count = 0
    guard = PatternGuard(policy="secrets")
    for name in GUIDES:
        expected_path = f"src/cairn/guides/{name}.md"
        groups = []
        start = 1
        while index < len(data["notes"]) and data["notes"][index].get("path") == expected_path:
            entry = data["notes"][index]
            if set(entry) != {"path", "start_line", "end_line", "page", "payload"}:
                _fail("release")
            payload = entry["payload"]
            if not isinstance(payload, dict) or set(payload) != {
                "text",
                "title",
                "topic",
                "label",
                "source_ref",
            }:
                _fail("release")
            text = payload["text"]
            if not isinstance(text, str) or not text or len(text.encode()) > 16384:
                _fail("release")
            lines = text.splitlines(keepends=True)
            end = start + len(lines) - 1
            page = f"docs-{name}-{len(groups) + 1:04d}"
            if (
                type(entry["start_line"]) is not int
                or type(entry["end_line"]) is not int
                or entry["start_line"] != start
                or entry["end_line"] != end
                or len(lines) > 80
                or entry["page"] != page
                or payload["title"] != page
                or payload["topic"] != page
                or payload["label"] != SCHEMA
                or payload["source_ref"]
                != f"{REPOSITORY}/blob/{release}/{expected_path}#L{start}-L{end}"
            ):
                _fail("release")
            groups.append(entry)
            entries[page] = dict(entry)
            start = end + 1
            index += 1
        if not groups:
            _fail("release")
        full = "".join(g["payload"]["text"] for g in groups)
        if (
            len(full.encode()) > MAX_FILE_BYTES  # the publisher's bound for one guide
            or guard.check(full)
            or re.search(r"\.ts\.net\b", full, re.I)
            or any(ord(c) < 32 and c not in "\n\t" for c in full)
        ):
            _fail("release")
        whole = chunk("full", full)
        isolated = []
        for entry in groups:
            offset = entry["start_line"] - 1
            isolated.extend(
                (s.kind, s.text, s.start_line + offset, s.end_line + offset)
                for s in chunk("part", entry["payload"]["text"])
            )
        if isolated != [(s.kind, s.text, s.start_line, s.end_line) for s in whole]:
            _fail("release")
        for entry in groups:
            offset = entry["start_line"] - 1
            entries[entry["page"]]["contexts"] = {
                (s.start_line - offset, s.end_line - offset, s.text): s.heading_path
                for s in whole
                if s.kind in CONTENT
                and entry["start_line"] <= s.start_line <= s.end_line <= entry["end_line"]
            }
        count += sum(s.kind in CONTENT for s in whole)
        if not any(s.kind in CONTENT for s in whole) or count > 1500:
            _fail("release")
    if index != len(data["notes"]):
        _fail("release")
    return project, entries


def _reader(identify_key, accounts, key, project):
    """The reader key's project id and its narrowed view of the store, after checking the key
    is a key, read-only, reaching exactly the one project, and that project is the release's.
    A fixed number of reads."""
    found = identify_key(key) if key else None
    if found is None:
        _fail("reader_unknown")
    principal, actor = found
    if principal.kind != "key" or principal.access != "read":
        _fail("reader_not_read_only")
    if principal.projects is None or len(principal.projects) != 1:
        _fail("reader_too_wide")
    memory = accounts.memory(principal, actor)
    if memory is None:
        _fail("reader_unknown")
    actual = memory.store.get_project(project["name"])
    if (
        actual is None
        or actual.name != project["name"]
        or actual.description != project["description"]
        or actual.kind != "documents"
    ):
        _fail("project")
    if principal.projects != (actual.id,):  # a set, even one holding only this project
        _fail("reader_too_wide")
    return actual.id, memory.store


@dataclass(frozen=True)
class Passage:
    guide: str  # the guide's name, one of GUIDES
    text: str
    url: str  # the passage's lines at the release's commit
    context: tuple[str, ...]  # the headings above it in the whole guide


@dataclass(frozen=True)
class _Kept:
    at: datetime
    project_id: str
    passages: tuple[Passage, ...]


@dataclass(frozen=True)
class _Failed:
    at: datetime
    reason: str


# The most characters one claim takes in a page's stored claims besides its text: its id,
# the ids of the passages it cites, and the JSON around them.
CLAIM_OVERHEAD = 512
# A stored claim's text is JSON with \u escapes: a character outside ASCII takes up to 12.
ESCAPED = 12


def _claims_most(entry) -> int:
    """The longest a page's stored claims (and its metrics) may be: ESCAPED times its
    passages' text, and CLAIM_OVERHEAD for each passage."""
    passages = entry["contexts"]
    return sum(ESCAPED * len(text) + CLAIM_OVERHEAD for _, _, text in passages)


def _span_most(entries) -> int:
    """The longest a passage's text or its stored headings may be: the artifact's longest."""
    return max(
        max(len(text), len(json.dumps(list(context), ensure_ascii=False)))
        for entry in entries.values()
        for (_, _, text), context in entry["contexts"].items()
    )


def _verified(identify_key, accounts, key, path, release) -> tuple[str, tuple[Passage, ...]]:
    """The release's passages, once the store holds exactly them: the artifact's pages and no
    other, each at version 1 or at a second version the no-model tidy made, every claim
    citing passages of its page's note whose lines, text, note title, `source_ref` and `via`
    are the artifact's, and every passage of the note cited.

    Counts come first: at most one page head more than the artifact has pages is read
    (no body, no claim text, no count of the project's pages), and a project that holds
    more pages, or a page more claims, than the artifact has pages or passages is refused
    at once (release_too_large). Every read after that is bounded by the artifact in rows
    and in text: one page's claims and metrics per artifact page, never its body, each
    read only when its stored text is at most `_claims_most` characters; at most as many
    passages as the artifact has, each read only when its text and headings are at most
    as long as the artifact's longest; at most as many notes as it has pages, never their
    text. A text longer than its bound is refused before it is read."""
    try:
        project, entries = _artifact(path, release)
    except Unavailable:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _fail("release")
    project_id, view = _reader(identify_key, accounts, key, project)
    allowed = {slug: len(entry["contexts"]) for slug, entry in entries.items()}
    heads = view.first_page_heads(project_id, len(entries) + 1)
    if len(heads) > len(entries) or any(
        head.slug not in allowed or head.claims > allowed[head.slug] for head in heads
    ):
        _fail("release_too_large")
    if {head.slug for head in heads} != set(entries):
        _fail("pages")
    if any(head.title != head.slug for head in heads):
        _fail("pages")
    claims = []
    for slug, entry in entries.items():
        found = view.page_claims(project_id, slug, _claims_most(entry))
        if found is None:
            _fail("pages")
        version, page_claims, metrics = found
        if page_claims is None or metrics is None or len(page_claims) > allowed[slug]:
            _fail("release_too_large")  # longer than the release allows, or grown since
        tidied = version == 2 and metrics.get("arranged_by") == "cairn"
        if not (version == 1 or tidied):
            _fail("pages")
        if not 1 <= len(page_claims) <= 80:
            _fail("evidence")
        claims.extend((slug, claim) for claim in page_claims)
    cited = list(dict.fromkeys(span_id for _, claim in claims for span_id in claim.span_ids))
    if len(cited) > sum(allowed.values()):
        _fail("release_too_large")
    spans = view.spans_within(cited, _span_most(entries))
    sources = list(dict.fromkeys(span.source_id for span in spans.values()))
    if len(sources) > len(entries):
        _fail("evidence")
    longest_ref = max(len(entry["payload"]["source_ref"]) for entry in entries.values())
    notes = view.note_sources(sources, longest_ref)
    covered = {slug: set() for slug in entries}
    for slug, claim in claims:
        entry = entries[slug]
        if not 1 <= len(claim.span_ids) <= 80:
            _fail("evidence")
        for span_id in claim.span_ids:
            span = spans.get(span_id)
            note = notes.get(span.source_id) if span else None
            if (
                note is None
                or note != (project_id, slug, entry["payload"]["source_ref"], "api")
                or claim.text != span.text
                or (span.start_line, span.end_line, span.text) not in entry["contexts"]
            ):
                _fail("evidence")
            covered[slug].add((span.start_line, span.end_line, span.text))
    passages = []
    for slug, entry in entries.items():
        if covered[slug] != set(entry["contexts"]):
            _fail("evidence")
        guide = entry["path"].removeprefix("src/cairn/guides/").removesuffix(".md")
        for (start, end, text), context in entry["contexts"].items():
            first, last = entry["start_line"] + start - 1, entry["start_line"] + end - 1
            url = f"{REPOSITORY}/blob/{release}/{entry['path']}#L{first}-L{last}"
            passages.append(Passage(guide, text, url, tuple(context)))
    return project_id, tuple(passages)


def _env_key(env_name) -> str:
    if not isinstance(env_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
        _fail("settings")
    return os.environ.get(env_name, "")


class Help:
    """The release's search, made once per app (help_routes). `search` runs in a worker
    thread; the module's docstring says what one costs. `clock`: what VERIFY_EVERY is
    measured with (an aware datetime)."""

    def __init__(self, identify_key, accounts, path, release, env_name, clock) -> None:
        self._identify_key = identify_key
        self._accounts = accounts
        self._path = path
        self._release = release
        self._env_name = env_name
        self._clock: Callable[[], datetime] = clock
        self._lock = threading.Lock()  # one verification at a time
        self._kept: _Kept | None = None
        self._failed: _Failed | None = None

    def passages(self) -> tuple[Passage, ...]:
        """The verified passages: verified again first when the last verification is
        VERIFY_EVERY old or none succeeded, else after checking the reader key again.
        Unavailable when either fails. A failed verification is remembered for
        RETRY_AFTER: until then a search checks the reader key and answers with the same
        reason, reading nothing else."""
        key = _env_key(self._env_name)
        project = {
            "name": f"cairn-docs-{self._release}",
            "description": f"cairn documentation release {self._release}; {SCHEMA}",
        }
        with self._lock:
            kept, failed, now = self._kept, self._failed, self._clock()
            if failed is not None and now - failed.at < RETRY_AFTER:
                _reader(self._identify_key, self._accounts, key, project)
                _fail(failed.reason)
            if kept is None or now - kept.at >= VERIFY_EVERY:
                self._kept, self._failed = None, None
                try:
                    project_id, passages = _verified(
                        self._identify_key, self._accounts, key, self._path, self._release
                    )
                except Unavailable as refused:
                    self._failed = _Failed(now, refused.reason)
                    raise
                self._kept = _Kept(now, project_id, passages)
                return passages
        project_id, _ = _reader(self._identify_key, self._accounts, key, project)
        if project_id != kept.project_id:
            _fail("project")
        return kept.passages

    def search(self, query: str, brand: Brand | None = None) -> list[dict[str, Any]]:
        """The first RESULTS kept passages whose text or headings hold every word of
        `query` (its first WORDS, split at spaces), ignoring case: those that hold the query
        as typed first, then the others, each group in the order of the guides. Each has its
        markers (`{{name}}`, `{{slug}}`, `{{SLUG}}`) filled from the product of `brand` as
        /docs fills them (markdown.fill) and is searched as filled; `html` is its text
        rendered as a claim's is (markdown.render: raw HTML stays text).
        Under a brand, as /docs: the operator's guide is not one of
        the guides, a passage that still names the software outside an identifier
        (IDENTIFIERS: CAIRN_API_KEY stays, `uvx cairn` does not) is not shown, and no
        passage links to the source repository
        (`url` is None)."""
        product = product_of(brand)
        wanted = " ".join(query.casefold().split())
        parts = wanted.split(" ")[:WORDS]
        typed: list[dict[str, Any]] = []  # hold the query as typed
        others: list[dict[str, Any]] = []  # hold every word of it
        for passage in self.passages():
            if len(passage.text) > 4096:
                continue
            if brand is not None and passage.guide == OPERATORS_GUIDE:
                continue
            text = markdown.fill(passage.text, product)
            context = " > ".join(markdown.fill(part, product) for part in passage.context)
            said = " ".join((context + " " + text).casefold().split())
            if brand is not None and names_the_software(context + " " + text):
                continue
            group = typed if wanted in said else others
            if len(group) < RESULTS and all(part in said for part in parts):
                group.append(
                    {
                        "text": text,
                        "html": _in_its_guide(markdown.render(text), passage.guide),
                        "url": None if brand is not None else passage.url,
                        "context": context[:1000],
                    }
                )
                if len(typed) == RESULTS:
                    break
        return (typed + others)[:RESULTS]


def _in_its_guide(html: Markup, guide: str) -> Markup:
    """Rendered `html` whose links to a heading of its own guide ("#claude-code") go to that
    guide's page: on the Help page they would lead nowhere. `guide` is one of the release's
    six names, checked when the artifact is read."""
    return Markup(str(html).replace('href="#', f'href="/docs/{guide}#'))


class HelpLink:
    """Says to every page this app renders that /help exists (templating.HELP), so the footer
    links to it. The app has it only when a release is configured."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        HELP.set(True)
        await self.app(scope, receive, send)


async def _query(request: Request) -> str:
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        _fail("form")
    body = b""
    async for part in request.stream():
        body += part
        if len(body) > 4096:
            _fail("form")
    try:
        form = parse_qs(body.decode("utf-8"), max_num_fields=2)
    except (ValueError, UnicodeError):
        _fail("form")
    if set(form) - {"q"} or len(form.get("q", [])) > 1:
        _fail("form")
    return form.get("q", [""])[0].strip()


def _logged(refused: Exception) -> None:
    """One record on the module's logger for a search that failed: its fixed reason code, or
    for an error this module did not raise, its class. Never the key, never the query."""
    if isinstance(refused, Unavailable):
        logger.warning("help unavailable: %s", refused.reason)
    else:
        logger.warning("help unavailable: error %s", type(refused).__name__)


def help_routes(identify_key, accounts, path, release, env_name, clock):
    """[/help] when a release is configured (any of the three settings given), else nothing:
    /help then answers as any unknown address does."""
    if path is None and release is None and env_name is None:
        return []
    helper = Help(identify_key, accounts, path, release, env_name, clock)

    async def help_page(request: Request):
        query, results, available = "", None, False
        if request.method == "POST":
            try:
                query = await _query(request)
            except Unavailable as refused:
                _logged(refused)
        asked = 0 < len(query) <= QUERY_MAX
        if asked:
            try:
                results = await run_in_threadpool(helper.search, query, brand_of(request))
                available = True
            except Exception as refused:
                _logged(refused)
        return _ui(
            request,
            "docs_helper.html",
            query=query[:QUERY_MAX],
            results=results,
            available=available,
            idle=not asked,
            told=request.method == "POST" and not asked,
        )

    return [Route("/help", help_page, methods=["GET", "POST"])]
