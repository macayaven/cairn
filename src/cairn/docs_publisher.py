"""Publish one checked-out documentation release through the existing REST API."""

import re
import subprocess
from pathlib import Path

from cairn.guard import PatternGuard
from cairn.spans import chunk

REPOSITORY = "https://github.com/macayaven/cairn"
SCHEMA = "cairn-docs-release-v1"
GUIDES = ("connect", "patterns", "sources", "security", "run", "privacy")
APPROVED_PATHS = tuple(f"src/cairn/guides/{name}.md" for name in GUIDES)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


class PublicationError(ValueError):
    """A fixed, safe message; never repository text, a URL or an HTTP body."""


MAX_FILE_BYTES = 128 * 1024
MAX_NOTE_BYTES = 16 * 1024
MAX_NOTES = 128
MAX_PASSAGES = 1500
CHUNK_LINES = 80
CONTENT_KINDS = {"paragraph", "bullet", "code", "table", "quote"}


def _verified_text(repo: Path, release: str, path: str) -> str:
    target = repo
    for part in Path(path).parts:
        target = target / part
        if target.is_symlink():
            raise PublicationError("documentation symlinks are refused")
    if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
        raise PublicationError("documentation file is missing or past its bound")
    mode = _git(repo, "ls-tree", release, "--", path)
    if not mode.startswith("100644 blob "):
        raise PublicationError("documentation must be an approved tracked regular file")
    if int(_git(repo, "cat-file", "-s", f"{release}:{path}")) > MAX_FILE_BYTES:
        raise PublicationError("committed documentation is past its bound")
    raw = target.read_bytes()
    committed = subprocess.run(
        ["git", "-C", str(repo), "show", f"{release}:{path}"],
        check=True,
        capture_output=True,
    ).stdout
    if raw != committed:
        raise PublicationError("documentation differs from the release")
    text = raw.decode("utf-8")
    if not text.strip():
        raise PublicationError("approved documentation guide is empty")
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise PublicationError("documentation contains unsupported control characters")
    if PatternGuard(policy="secrets").check(text) or re.search(
        r"(?i)(?:[a-z0-9-]+\.)+ts\.net\b", text
    ):
        raise PublicationError("documentation secret scan refused the release")
    return text


def build_release(repo: Path, release: str) -> dict:
    try:
        return _build_release(repo, release)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise PublicationError("cannot verify the documentation repository") from None


def _groups(text: str) -> list[tuple[int, int, str]]:
    """Contiguous source ranges; no boundary may divide a whole-document passage."""
    lines = text.splitlines(keepends=True)
    original = chunk("docs", text)
    boundaries = sorted(
        {len(lines)} | {s.end_line for s in original} | {s.start_line - 1 for s in original}
    )
    groups = []
    offset = 0
    while offset < len(lines):
        for end in reversed(boundaries):
            if not offset < end <= offset + CHUNK_LINES:
                continue
            part = "".join(lines[offset:end])
            if len(part.encode()) > MAX_NOTE_BYTES:
                continue
            expected = [s for s in original if offset < s.start_line and s.end_line <= end]
            local = chunk("docs", part)
            # Re-parsing an isolated range must not turn prose into code/front matter,
            # split lists, or otherwise change the complete document's passages.
            if [(s.kind, s.text, s.start_line + offset, s.end_line + offset) for s in local] != [
                (s.kind, s.text, s.start_line, s.end_line) for s in expected
            ]:
                continue
            if not any(s.kind in CONTENT_KINDS for s in local):
                continue
            groups.append((offset, end, part))
            offset = end
            break
        else:
            raise PublicationError("documentation passage cannot fit a safe bounded note")
    return groups


def _build_release(repo: Path, release: str) -> dict:
    """Build the finite artifact without contacting a server."""
    repo = repo.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{40}", release) or _git(repo, "rev-parse", "HEAD") != release:
        raise PublicationError("release must be the checked-out full commit")
    if _git(repo, "remote", "get-url", "origin") not in {
        REPOSITORY,
        REPOSITORY + ".git",
        "git@github.com:macayaven/cairn.git",
    }:
        raise PublicationError("origin must be the approved documentation repository")
    _git(repo, "diff", "--exit-code", "--cached", release, "--", *APPROVED_PATHS)
    notes = []
    passage_count = 0
    for name, path in zip(GUIDES, APPROVED_PATHS, strict=True):
        text = _verified_text(repo, release, path)
        for number, (offset, end, part) in enumerate(_groups(text), start=1):
            spans = [span for span in chunk("docs", part) if span.kind in CONTENT_KINDS]
            passage_count += len(spans)
            if passage_count > MAX_PASSAGES:
                raise PublicationError("documentation release has too many passages")
            if len(part.encode()) > MAX_NOTE_BYTES or not spans:
                raise PublicationError("documentation chunk has no content or is past its bound")
            page = f"docs-{name}-{number:04d}"
            notes.append(
                {
                    "path": path,
                    "start_line": offset + 1,
                    "end_line": end,
                    "page": page,
                    "payload": {
                        "text": part,
                        "title": page,
                        "topic": page,
                        "label": SCHEMA,
                        "source_ref": f"{REPOSITORY}/blob/{release}/{path}#L{offset + 1}-L{end}",
                    },
                }
            )
    if len(notes) > MAX_NOTES:
        raise PublicationError("documentation release has too many notes")
    return {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "release": release,
        "project": {
            "name": f"cairn-docs-{release}",
            "description": f"cairn documentation release {release}; {SCHEMA}",
        },
        "notes": notes,
    }


MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUESTS = 4096


class _API:
    def __init__(self, base_url: str, key: str, transport: object) -> None:
        import httpx

        self.client = httpx.Client(
            base_url=base_url,
            headers={"x-api-key": key},
            transport=transport,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
        )
        self.requests = 0

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        import json

        import httpx

        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise PublicationError("documentation request bound exceeded")
        try:
            with self.client.stream(method, path, json=payload, follow_redirects=False) as response:
                if response.status_code not in {200, 201}:
                    raise PublicationError(
                        "documentation API refused the request; release incomplete"
                    )
                raw = bytearray()
                for block in response.iter_bytes(chunk_size=8192):
                    raw.extend(block)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise PublicationError("documentation API response past its bound")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise PublicationError("documentation API returned invalid data")
                return data
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, PublicationError):
                raise
            raise PublicationError("documentation API request failed; release incomplete") from None


def _check_page(api: _API, project: str, entry: dict) -> None:
    from urllib.parse import quote

    route = f"/api/projects/{project}/pages/{entry['page']}"
    page = api.request("GET", route)
    if (
        page.get("project") != project
        or page.get("slug") != entry["page"]
        or page.get("title") != entry["page"]
        or not (
            (page.get("version") == 1 and page.get("versions") == [1])
            or (
                page.get("version") == 2
                and page.get("versions") == [1, 2]
                and isinstance(page.get("metrics"), dict)
                and page["metrics"].get("arranged_by") == "cairn"
            )
        )
    ):
        raise PublicationError("documentation page does not match this release")
    claims = page.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= CHUNK_LINES:
        raise PublicationError("documentation claims are missing or past their bound")
    expected = {
        (span.start_line, span.end_line, span.text)
        for span in chunk("docs", entry["payload"]["text"])
        if span.kind in CONTENT_KINDS
    }
    seen = set()
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("id"), str):
            raise PublicationError("documentation claim is malformed")
        evidence = api.request(
            "GET", route + "/claims/" + quote(claim["id"], safe="") + "/evidence"
        )
        passages = evidence.get("passages")
        if not isinstance(passages, list) or not 1 <= len(passages) <= CHUNK_LINES:
            raise PublicationError("documentation evidence is missing or past its bound")
        for passage in passages:
            if not isinstance(passage, dict):
                raise PublicationError("documentation passage is malformed")
            found = (passage.get("start_line"), passage.get("end_line"), passage.get("text"))
            if (
                found not in expected
                or passage.get("source_ref") != entry["payload"]["source_ref"]
                or claim.get("text") != passage.get("text")
                or passage.get("via") != "api"
                or passage.get("note_title") != entry["page"]
            ):
                raise PublicationError("documentation evidence does not match release provenance")
            seen.add(found)
    if seen != expected:
        raise PublicationError("documentation page is missing release evidence")


def _preflight(api: _API, artifact: dict, *, complete: bool = False) -> None:
    expected = artifact["project"]
    projects = api.request("GET", "/api/projects").get("projects")
    if not isinstance(projects, list) or len(projects) != 1:
        raise PublicationError("credential must reach only the documentation release project")
    project = projects[0]
    if not isinstance(project, dict) or any(project.get(k) != v for k, v in expected.items()):
        raise PublicationError("destination is not this documentation release project")
    if project.get("kind") != "documents" or project.get("models") != "never":
        raise PublicationError("documentation destination must be documents with models never")
    entries = {entry["page"]: entry for entry in artifact["notes"]}
    seen = set()
    after = ""
    for _ in range(3):  # at most 128 pages, existing API returns at most 100 at once
        from urllib.parse import quote

        query = "?limit=100" + ("&after=" + quote(after, safe="") if after else "")
        part = api.request("GET", f"/api/projects/{expected['name']}/pages" + query)
        pages = part.get("pages")
        if not isinstance(pages, list) or len(pages) > 100:
            raise PublicationError("documentation page list is malformed")
        for page in pages:
            slug = page.get("slug") if isinstance(page, dict) else None
            if not isinstance(slug, str) or slug not in entries or slug in seen:
                raise PublicationError("destination contains an unexpected documentation page")
            seen.add(slug)
            _check_page(api, expected["name"], entries[slug])
        following = part.get("pages_next")
        if following is None:
            break
        if not pages or following != pages[-1]["slug"] or following <= after:
            raise PublicationError("documentation pagination is malformed")
        after = following
    else:
        raise PublicationError("documentation page list past its bound")
    if complete and seen != set(entries):
        raise PublicationError("documentation release is incomplete")


def publish_release(
    repo: Path, release: str, *, base_url: str, key_env: str, transport: object = None
) -> dict:
    """Explicit writer operation. Rebuild and scan every note before the first request."""
    import os
    from urllib.parse import urlsplit

    artifact = build_release(repo, release)
    try:
        url = urlsplit(base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError
        _ = url.port
    except ValueError:
        raise PublicationError("destination must be an HTTPS origin without userinfo") from None
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", key_env):
        raise PublicationError("credential must be a named process environment variable")
    key = os.environ.get(key_env, "")
    if not key or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key):
        raise PublicationError("documentation writer environment variable is absent or invalid")
    api = _API(base_url, key, transport)
    try:
        _preflight(api, artifact)
        created = 0
        for entry in artifact["notes"]:
            answer = api.request(
                "POST", f"/api/projects/{artifact['project']['name']}/notes", entry["payload"]
            )
            if (
                answer.get("page") != entry["page"]
                or answer.get("status") not in {"saved", "unchanged"}
                or not (
                    answer.get("version") == 1
                    or (
                        answer.get("version") == 2
                        and answer.get("status") == "unchanged"
                        and answer.get("created") is False
                    )
                )
                or type(answer.get("created")) is not bool
                or answer.get("violations") != []
            ):
                raise PublicationError(
                    "documentation publication did not finish; rerun after review"
                )
            created += int(answer["created"])
        _preflight(api, artifact, complete=True)
        return {"status": "published", "notes": len(artifact["notes"]), "created": created}
    except (KeyError, TypeError):
        raise PublicationError("documentation API returned malformed release data") from None
    finally:
        api.client.close()


def main(argv: list[str] | None = None) -> int:
    """Dry-run by default; stdout carries counts only, errors quote no input but the name of
    an --output file that holds something other than this release."""
    import argparse
    import json
    import os
    import sys

    class Parser(argparse.ArgumentParser):
        def error(self, message: str) -> None:
            raise PublicationError("invalid documentation publisher arguments; see --help")

    parser = Parser(description=__doc__, prog="python -m cairn.docs_publisher")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--release", required=True, help="checked-out full 40-character commit")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="artifact file; never overwritten, and accepted as it is when it holds this release",
    )
    parser.add_argument("--publish", action="store_true", help="explicitly send notes to the API")
    parser.add_argument("--base-url", help="HTTPS origin of the existing cairn service")
    parser.add_argument("--key-env", help="name of the process variable holding the writer key")
    try:
        args = parser.parse_args(argv)
        if args.publish and (not args.base_url or not args.key_env):
            raise PublicationError("publish requires --base-url and --key-env")
        artifact = build_release(args.repo, args.release)
        data = (json.dumps(artifact, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        try:
            with open(
                args.output, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)
            ) as out:
                out.write(data)
        except FileExistsError:
            # A second run of one release: the file it wrote before is accepted as it is.
            with open(args.output, "rb") as existing:
                if existing.read(len(data) + 1) != data:
                    raise PublicationError(
                        f"{args.output} already exists and holds something other than this"
                        " release; remove it or choose another --output"
                    ) from None
        result = {"status": "dry-run", "notes": len(artifact["notes"])}
        if args.publish:
            result = publish_release(
                args.repo, args.release, base_url=args.base_url, key_env=args.key_env
            )
        print(json.dumps(result))
        return 0
    except PublicationError as error:
        print(f"cairn docs: {error}", file=sys.stderr)
    except Exception:
        # OS/HTTP/parser exceptions can include credentials or repository text.
        print("cairn docs: operation failed; no release completion confirmed", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
