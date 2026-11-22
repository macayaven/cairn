"""Canonical in-memory fake GitHub Release API hub and runner for C6-A release step tests."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci-cd.yml"
DEFAULT_COMMIT = "1a2b3c4d" * 5
DEFAULT_MAIN_HEAD = "9f8e7d6c" * 5
OTHER_COMMIT = "5e5e5e5e" * 5
DEFAULT_RUN_ID = "4242"
DEFAULT_MARK = f"<!-- cairn-ci-cd run {DEFAULT_RUN_ID} -->"
DEFAULT_UPLOADED_AT = "2026-11-20T12:00:00Z"


def release_files_of(version: str, notes: bytes = b"the notes\n") -> dict[str, bytes]:
    """Build deterministic artifact payload bytes matching `package` + SHA256SUMS."""
    artifacts = {
        f"cairn_memory-{version}-py3-none-any.whl": b"a wheel",
        f"cairn_memory-{version}.tar.gz": b"a source archive",
        "notes.md": notes,
    }
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in artifacts.items()
    )
    return {**artifacts, "SHA256SUMS": sums.encode("utf-8")}


def populate_release_dir(
    release_dir: Path,
    tag: str,
    *,
    notes: bytes = b"the notes\n",
    overrides: dict[str, bytes] | None = None,
) -> dict[str, bytes]:
    """Write release artifacts (excluding notes.md for attached assets return value) to disk."""
    version = tag.removeprefix("v").split("-rc.", 1)[0]
    files = release_files_of(version, notes=notes)
    if overrides:
        files.update(overrides)
    release_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (release_dir / name).write_bytes(data)
    return {k: v for k, v in files.items() if k != "notes.md"}


def extract_release_script(workflow_path: Path = WORKFLOW_PATH) -> str:
    """Extract exact Python payload of job `release`'s `Create the release` step."""
    wf = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    step = next(s for s in wf["jobs"]["release"]["steps"] if s.get("name") == "Create the release")
    raw = step["run"]
    marker = "python3 -I - <<'PY'\n"
    start = raw.index(marker) + len(marker)
    end = raw.rindex("\nPY")
    return raw[start:end] + "\n"


def make_release_record(
    tag: str,
    prerelease: bool,
    *,
    rid: int = 77,
    draft: bool = False,
    title: str | None = None,
    body: str | None = None,
    notes_text: str = "the notes\n",
    run_id: str = DEFAULT_RUN_ID,
    asset_files: dict[str, bytes] | None = None,
    asset_overrides: dict[str, dict[str, Any]] | None = None,
    asset_id_base: int | None = None,
) -> dict[str, Any]:
    """Construct a synthetic GitHub release dict + embedded `_bytes` per asset for ReleaseHub."""
    version = tag.removeprefix("v").split("-rc.", 1)[0]
    if asset_files is None:
        all_files = release_files_of(version, notes=notes_text.encode("utf-8"))
        asset_files = {k: v for k, v in all_files.items() if k != "notes.md"}
    mark = f"<!-- cairn-ci-cd run {run_id} -->"
    if body is None:
        body = f"{notes_text}\n{mark}\n"
    base_id = asset_id_base if asset_id_base is not None else rid * 10
    assets = []
    for idx, (name, data) in enumerate(sorted(asset_files.items())):
        override = (asset_overrides or {}).get(name, {})
        assets.append(
            {
                "id": override.get("id", base_id + idx),
                "name": override.get("name", name),
                "state": override.get("state", "uploaded"),
                "size": override.get("size", len(data)),
                "updated_at": override.get("updated_at", DEFAULT_UPLOADED_AT),
                "_bytes": override.get("_bytes", data),
            }
        )
    return {
        "id": rid,
        "tag_name": tag,
        "name": tag if title is None else title,
        "draft": draft,
        "prerelease": prerelease,
        "body": body,
        "assets": assets,
    }


def strip_asset_bytes(rel: dict[str, Any]) -> dict[str, Any]:
    """Serialize a release dict without internal `_bytes` fields (as GitHub REST returns it)."""
    clean = copy.deepcopy(rel)
    for asset in clean.get("assets") or []:
        asset.pop("_bytes", None)
    return clean


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    calls: list[list[str]]
    sleeps: list[float]
    writes: list[list[str]]

    def single_error(self) -> str:
        errors = [line for line in self.stdout.splitlines() if line.startswith("::error::")]
        assert len(errors) == 1, f"expected 1 ::error:: line, got {errors!r}"
        assert errors[0].count(". ") == 0, errors[0]
        return errors[0]


@dataclass
class ReleaseHub:
    """In-memory fake GitHub API backend modelling lag, faults, and concurrent user edits."""

    tag: str
    tag_sha: str | None = DEFAULT_COMMIT
    main_head: str = DEFAULT_MAIN_HEAD
    releases: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 88
    next_asset_id: int = 880

    # Fault & lag knobs
    tag_fails_at_read: int = 0
    tag_mutates_at_read: tuple[int, str | None] | None = None
    tag_before_publish: str | None = None
    tag_after_publish: str | None = None
    tag_after_create: str | None = None

    list_fails: bool = False
    list_fails_after_write: bool = False
    paginate_multi_page: bool = False

    read_fails_at: dict[int, tuple[int, str]] = field(
        default_factory=dict
    )  # 1-based read -> (http, msg)
    deleted_before_read: int = 0

    fetch_fails_at: int = 0
    download_fails_draft: bool = False
    download_fails_published: bool = False

    create_fault: str | None = None  # "refused" | "lost-ack" | "cut-after-1"
    publish_fault: str | None = None  # "refused" | "lost-ack"

    # Lag configuration: list of callable(hub) -> list[dict] OR static release list for k looks
    stale_override: tuple[list[dict[str, Any]], int | None] | None = None
    made_shown_from_look: int = 1
    published_shown_from_look: int = 1
    person_listed_from_look: int = 1
    person_shows_behind: bool = False
    names_listed_at_first: list[str] | None = None
    names_listed_for_looks: int | None = None

    # Arbitrary callbacks: invoked before/after events or call numbers
    before_call: Callable[[ReleaseHub, list[str]], None] | None = None
    after_call: Callable[[ReleaseHub, list[str]], None] | None = None
    on_once_made: Callable[[ReleaseHub], None] | None = None
    on_before_publish: Callable[[ReleaseHub], None] | None = None
    on_once_public: Callable[[ReleaseHub], None] | None = None
    on_before_read: dict[int, Callable[[ReleaseHub], None]] = field(default_factory=dict)
    on_before_fetch: dict[int, Callable[[ReleaseHub], None]] = field(default_factory=dict)
    on_after_fetch: dict[int, Callable[[ReleaseHub], None]] = field(default_factory=dict)

    # Telemetry (reset per attempt except persistent release state)
    calls: list[list[str]] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    writes: list[list[str]] = field(default_factory=list)
    deletes: list[list[str]] = field(default_factory=list)
    boundaries: list[int] = field(default_factory=list)
    tag_recreated_at: str | None = None

    # Per-attempt counters
    _written: bool = False
    _tag_reads: int = 0
    _looks: int = 0
    _reads: int = 0
    _fetches: int = 0
    _stale_looks: int = 0
    _behind_snapshot: list[dict[str, Any]] | None = None
    _behind_looks: int = 0
    _behind_from: int = 1
    _person_stale_snapshot: list[dict[str, Any]] | None = None
    _person_stale_looks: int = 0
    _made_looks: int = 0

    def reset_attempt(self) -> None:
        self.calls = []
        self.sleeps = []
        self.writes = []
        self.boundaries = []
        self._written = False
        self._tag_reads = 0
        self._looks = 0
        self._reads = 0
        self._fetches = 0
        self._stale_looks = 0
        self._behind_snapshot = None
        self._behind_looks = 0
        self._behind_from = 1
        self._person_stale_snapshot = None
        self._person_stale_looks = 0
        self._made_looks = 0

    def snapshot_releases(self) -> list[dict[str, Any]]:
        return [strip_asset_bytes(r) for r in self.releases]

    def _begin_write_lag(self, show_from: int) -> None:
        self._written = True
        if show_from > 1:
            self._behind_snapshot = copy.deepcopy(self.releases)
            self._behind_from = show_from
            self._behind_looks = 0

    def by_hand_mutate(self, fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
        if self.person_listed_from_look > 1 and self._person_stale_snapshot is None:
            self._person_stale_snapshot = copy.deepcopy(self.releases)
            self._person_stale_looks = 0
        self.releases = fn(self.releases)
        if self.person_shows_behind and self._behind_snapshot is not None:
            self._behind_snapshot = fn(self._behind_snapshot)

    def _alloc_id(self) -> int:
        rid = self.next_id
        self.next_id = rid + 1 if rid != 88 else 99
        return rid

    def handle_gh(
        self, args: list[str], *, cwd: Path, binary: bool = False
    ) -> tuple[int, bytes | str, bytes | str]:
        self.calls.append(list(args))
        if any(r.get("draft") for r in self.releases):
            self.boundaries.append(len(self.calls) - 1)
        if self.before_call is not None:
            self.before_call(self, args)

        rc, out, err = self._dispatch(args, cwd=cwd)
        if self.after_call is not None:
            self.after_call(self, args)

        if binary:
            out_b = out if isinstance(out, bytes) else out.encode("utf-8")
            err_b = err if isinstance(err, bytes) else err.encode("utf-8")
            return rc, out_b, err_b
        out_s = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
        err_s = err.decode("utf-8", "replace") if isinstance(err, bytes) else err
        return rc, out_s, err_s

    def _dispatch(self, args: list[str], *, cwd: Path) -> tuple[int, bytes | str, str]:
        if args == ["--version"]:
            return (
                0,
                "gh version 2.102.0 (2026-01-01)\nhttps://github.com/cli/cli/releases/tag/v2.102.0\n",
                "",
            )

        # Catch any explicit delete commands or HTTP DELETE
        if (args[0] == "release" and args[1].startswith("delete")) or (
            args[0] == "api" and any(a.upper() == "DELETE" for a in args)
        ):
            self.writes.append(list(args))
            self.deletes.append(list(args))
            return 0, "", ""

        if len(args) >= 2 and args[0] == "release" and args[1] == "create":
            return self._handle_release_create(args[2:], cwd=cwd)

        if args[0] == "api":
            return self._handle_api(args[1:])

        return 2, "", f"unsupported gh call: {args!r}\n"

    def _handle_release_create(self, args: list[str], *, cwd: Path) -> tuple[int, str, str]:
        self.writes.append(["release", "create", *args])
        tag = args[0]
        verify_tag = "--verify-tag" in args
        if verify_tag and not self.tag_sha:
            return (
                1,
                "",
                f"tag {tag} doesn't exist in the repo, aborting due to --verify-tag flag\n",
            )

        draft = "--draft" in args
        pre = "--prerelease" in args
        target = DEFAULT_COMMIT
        title = None
        body = ""
        files: list[str] = []
        i = 1
        while i < len(args):
            tok = args[i]
            if tok in ("--draft", "--verify-tag", "--prerelease", "--latest=false"):
                i += 1
            elif tok == "--target":
                target = args[i + 1]
                i += 2
            elif tok == "--title":
                title = args[i + 1]
                i += 2
            elif tok == "--notes-file":
                body = Path(args[i + 1]).read_bytes().decode("utf-8")
                i += 2
            else:
                files.append(tok)
                i += 1

        if self.create_fault == "refused":
            return 1, "", "gh: Validation Failed (HTTP 422)\n"

        rid = self._alloc_id()
        attached_names = files[:1] if self.create_fault == "cut-after-1" else files
        assets = []
        for idx, name in enumerate(attached_names):
            data = (cwd / name).read_bytes()
            assets.append(
                {
                    "id": rid * 10 + idx,
                    "name": name,
                    "size": len(data),
                    "state": "uploaded",
                    "updated_at": DEFAULT_UPLOADED_AT,
                    "_bytes": data,
                }
            )
        new_rel = {
            "id": rid,
            "tag_name": tag,
            "target_commitish": target,
            "name": title,
            "draft": draft,
            "prerelease": pre,
            "body": body,
            "assets": assets,
        }
        self._begin_write_lag(self.made_shown_from_look)
        self._made_looks = 0
        self.releases.append(new_rel)
        if self.tag_after_create is not None:
            self.tag_sha = None if self.tag_after_create == "gone" else self.tag_after_create
        if self.on_once_made is not None:
            self.on_once_made(self)
        if self.create_fault in ("lost-ack", "cut-after-1"):
            return 1, "", "gh: Server Error (HTTP 502)\n"
        return 0, "", ""

    def _handle_api(self, args: list[str]) -> tuple[int, bytes | str, str]:
        if args[:2] == ["-H", "Accept: application/vnd.github.sha"] and len(args) == 3:
            self._tag_reads += 1
            if self.tag_fails_at_read == self._tag_reads:
                return 1, '{"message":"Server Error"}\n', "gh: Server Error (HTTP 502)\n"
            if self.tag_mutates_at_read and self.tag_mutates_at_read[0] == self._tag_reads:
                new_val = self.tag_mutates_at_read[1]
                self.tag_sha = None if new_val == "gone" else new_val
            if not self.tag_sha:
                return (
                    1,
                    f'{{"message":"No commit found for SHA: tags/{self.tag}","status":"422"}}\n',
                    f"gh: No commit found for SHA: tags/{self.tag} (HTTP 422)\n",
                )
            return 0, self.tag_sha, ""

        if args[0] == "--paginate":
            if self.list_fails or (self.list_fails_after_write and self._written):
                return 1, "", "HTTP 502\n"
            self._looks += 1
            shown = self.releases
            if self._behind_snapshot is not None:
                self._behind_looks += 1
                if self._behind_looks < self._behind_from:
                    shown = self._behind_snapshot
            if self._person_stale_snapshot is not None:
                self._person_stale_looks += 1
                if self._person_stale_looks < self.person_listed_from_look:
                    shown = self._person_stale_snapshot
            if self.stale_override is not None:
                override_list, until_look = self.stale_override
                self._stale_looks += 1
                if until_look is None or self._stale_looks < until_look:
                    shown = override_list
            payload = [strip_asset_bytes(r) for r in copy.deepcopy(shown)]
            if self.names_listed_at_first is not None and any(r["id"] == 88 for r in self.releases):
                self._made_looks += 1
                if (
                    self.names_listed_for_looks is None
                    or self._made_looks <= self.names_listed_for_looks
                ):
                    for r in payload:
                        if r["id"] == 88:
                            r["assets"] = [
                                {"name": n, "size": 1, "id": 880 + idx}
                                for idx, n in enumerate(self.names_listed_at_first)
                            ]
            if self.paginate_multi_page and len(payload) > 1:
                mid = len(payload) // 2
                return 0, json.dumps(payload[:mid]) + "\n" + json.dumps(payload[mid:]), ""
            return 0, json.dumps(payload), ""

        if args[:2] == ["--method", "PATCH"]:
            self.writes.append(["api", *args])
            endpoint = next(a for a in args if a.startswith("repos/"))
            rid = int(endpoint.rsplit("/", 1)[-1])
            if self.tag_before_publish is not None:
                self.tag_sha = (
                    None if self.tag_before_publish == "gone" else self.tag_before_publish
                )
            if self.on_before_publish is not None:
                self.on_before_publish(self)
            target_rel = next((r for r in self.releases if r["id"] == rid), None)
            if target_rel is None:
                return 1, '{"message":"Not Found"}\n', "gh: Not Found (HTTP 404)\n"
            self._begin_write_lag(self.published_shown_from_look)
            target_rel["draft"] = False
            if not self.tag_sha:
                self.tag_sha = target_rel.get("target_commitish", self.main_head) or self.main_head
                self.tag_recreated_at = self.tag_sha
            if self.tag_after_publish is not None:
                self.tag_sha = None if self.tag_after_publish == "gone" else self.tag_after_publish
            if self.on_once_public is not None:
                self.on_once_public(self)
            if self.publish_fault == "lost-ack":
                return 1, "", "gh: Server Error (HTTP 502)\n"
            if self.publish_fault == "refused":
                return 1, "", "gh: Forbidden (HTTP 403)\n"
            return 0, "", ""

        if args[:3] == ["--allow-escape-sequences", "-H", "Accept: application/octet-stream"]:
            self._fetches += 1
            if hook := self.on_before_fetch.get(self._fetches):
                hook(self)
            if self.fetch_fails_at == self._fetches:
                return 1, b"", "gh: Bad Gateway (HTTP 502)\n"
            aid = int(args[3].rsplit("/", 1)[-1])
            for rel in self.releases:
                for asset in rel.get("assets") or []:
                    if asset.get("id") == aid and "_bytes" in asset:
                        if (self.download_fails_draft and rel.get("draft")) or (
                            self.download_fails_published and not rel.get("draft")
                        ):
                            return 1, b"", "gh: Bad Gateway (HTTP 502)\n"
                        data = bytes(asset["_bytes"])
                        if hook_after := self.on_after_fetch.get(self._fetches):
                            hook_after(self)
                        return 0, data, ""
            return 1, b'{"message":"Not Found","status":"404"}\n', "gh: Not Found (HTTP 404)\n"

        # GET repos/{repo}/releases/{rid}
        if len(args) == 1 and re.fullmatch(r"repos/[^/]+/[^/]+/releases/\d+", args[0]):
            self._reads += 1
            if hook := self.on_before_read.get(self._reads):
                hook(self)
            if self.deleted_before_read == self._reads:
                self.releases = []
            if fault := self.read_fails_at.get(self._reads):
                status, msg = fault
                return 1, f'{{"status":"{status}"}}\n', f"gh: {msg} (HTTP {status})\n"
            rid = int(args[0].rsplit("/", 1)[-1])
            found = next((r for r in self.releases if r["id"] == rid), None)
            if found is None:
                return 1, '{"message":"Not Found","status":"404"}\n', "gh: Not Found (HTTP 404)\n"
            return 0, json.dumps(strip_asset_bytes(found)), ""

        return 2, "", f"unexpected gh api call: {args!r}\n"


_COMPILED_RELEASE_SCRIPT = None


def _compiled_release_script():
    global _COMPILED_RELEASE_SCRIPT
    if _COMPILED_RELEASE_SCRIPT is None:
        _COMPILED_RELEASE_SCRIPT = compile(extract_release_script(), "<create_the_release>", "exec")
    return _COMPILED_RELEASE_SCRIPT


def run_release_in_process(
    hub: ReleaseHub,
    tmp_path: Path,
    *,
    tag: str = "v1.2.3",
    candidate: str = "",
    sha: str = DEFAULT_COMMIT,
    run_id: str = DEFAULT_RUN_ID,
    notes: bytes = b"the notes\n",
    file_overrides: dict[str, bytes] | None = None,
    reset: bool = True,
) -> RunResult:
    """Compile and run the exact YAML release script in-process against ReleaseHub."""
    release_dir = tmp_path / "release"
    populate_release_dir(release_dir, tag, notes=notes, overrides=file_overrides)
    if reset:
        hub.reset_attempt()

    prev_cwd = Path.cwd()
    prev_env = dict(os.environ)
    prev_run = subprocess.run
    prev_sleep = time.sleep
    out_buf = io.StringIO()
    err_buf = io.StringIO()

    def fake_subprocess_run(cmd, *args, **kwargs):
        assert cmd[0] == "gh", cmd
        binary = not kwargs.get("text", False)
        rc, out, err = hub.handle_gh(list(cmd[1:]), cwd=Path.cwd(), binary=binary)
        return subprocess.CompletedProcess(cmd, rc, out, err)

    def fake_sleep(seconds: float) -> None:
        hub.sleeps.append(seconds)

    ns: dict[str, Any] = {"__name__": "cairn_ci_release_step"}
    code = _compiled_release_script()
    try:
        os.environ.update(
            {
                "RUNNER_TEMP": str(tmp_path),
                "GITHUB_REF_NAME": tag,
                "GITHUB_SHA": sha,
                "GITHUB_RUN_ID": run_id,
                "GH_REPO": "owner/repo",
                "CANDIDATE": candidate,
            }
        )
        subprocess.run = fake_subprocess_run
        time.sleep = fake_sleep
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            exec(code, ns)
            rc = ns["main"]()
    finally:
        subprocess.run = prev_run
        time.sleep = prev_sleep
        os.environ.clear()
        os.environ.update(prev_env)
        os.chdir(prev_cwd)

    return RunResult(
        returncode=int(rc),
        stdout=out_buf.getvalue(),
        stderr=err_buf.getvalue(),
        calls=list(hub.calls),
        sleeps=list(hub.sleeps),
        writes=list(hub.writes),
    )


SUBPROCESS_GH = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_file = Path(os.environ["HUB_STATE"])
state = json.loads(state_file.read_text(encoding="utf-8"))
args = sys.argv[1:]
state["calls"].append(args)

if args == ["--version"]:
    ver = state.get("gh_version", "2.102.0")
    print(f"gh version {ver} (2026-01-01)")
    print(f"https://github.com/cli/cli/releases/tag/v{ver}")
    state_file.write_text(json.dumps(state), encoding="utf-8")
    sys.exit(0)

rc = 0
out = b""
err = ""

if args[:2] == ["release", "create"]:
    state["writes"].append(["release", "create", *args[2:]])
    tag = args[2]
    verify = "--verify-tag" in args
    if verify and not state.get("tag_sha"):
        rc = 1
        err = f"tag {tag} doesn't exist in the repo, aborting due to --verify-tag flag\n"
    else:
        draft = "--draft" in args
        pre = "--prerelease" in args
        title = None
        target = state.get("tag_sha")
        body = ""
        files = []
        i = 3
        while i < len(args):
            t = args[i]
            if t in ("--draft", "--verify-tag", "--prerelease", "--latest=false"):
                i += 1
            elif t == "--title":
                title = args[i + 1]
                i += 2
            elif t == "--target":
                target = args[i + 1]
                i += 2
            elif t == "--notes-file":
                body = Path(args[i + 1]).read_bytes().decode("utf-8")
                i += 2
            else:
                files.append(t)
                i += 1
        rid = state.get("next_id", 88)
        state["next_id"] = rid + 1
        assets = []
        for idx, name in enumerate(files):
            raw = Path(name).read_bytes()
            assets.append({
                "id": rid * 10 + idx,
                "name": name,
                "state": "uploaded",
                "size": len(raw),
                "updated_at": "2026-11-20T12:00:00Z",
                "_hex": raw.hex(),
            })
        state["releases"].append({
            "id": rid,
            "tag_name": tag,
            "target_commitish": target,
            "name": title,
            "draft": draft,
            "prerelease": pre,
            "body": body,
            "assets": assets,
        })
elif args[0] == "api" and args[1:3] == ["-H", "Accept: application/vnd.github.sha"]:
    sha = state.get("tag_sha")
    if not sha:
        rc = 1
        err = "gh: No commit found (HTTP 422)\n"
    else:
        out = sha.encode("utf-8")
elif args[0] == "api" and args[1] == "--paginate":
    payload = [
        {**r, "assets": [{k: v for k, v in a.items() if k != "_hex"} for a in r["assets"]]}
        for r in state["releases"]
    ]
    out = json.dumps(payload).encode("utf-8")
elif args[0] == "api" and args[1:3] == ["--method", "PATCH"]:
    state["writes"].append(["api", *args[1:]])
    rid = int(next(a for a in args if a.startswith("repos/")).rsplit("/", 1)[-1])
    for r in state["releases"]:
        if r["id"] == rid:
            r["draft"] = False
elif args[:2] == ["api", "--allow-escape-sequences"] and args[2:4] == [
    "-H",
    "Accept: application/octet-stream",
]:
    aid = int(args[4].rsplit("/", 1)[-1])
    found = None
    for r in state["releases"]:
        for a in r["assets"]:
            if a["id"] == aid:
                found = bytes.fromhex(a["_hex"])
    if found is None:
        rc = 1
        err = "gh: Not Found (HTTP 404)\\n"
    else:
        out = found
elif args[0] == "api" and len(args) == 2 and "/releases/" in args[1]:
    rid = int(args[1].rsplit("/", 1)[-1])
    rel = next((r for r in state["releases"] if r["id"] == rid), None)
    if rel is None:
        rc = 1
        err = "gh: Not Found (HTTP 404)\\n"
    else:
        stripped = [{k: v for k, v in a.items() if k != "_hex"} for a in rel["assets"]]
        clean = {**rel, "assets": stripped}
        out = json.dumps(clean).encode("utf-8")
else:
    rc = 2
    err = f"unexpected: {args!r}\n"

state_file.write_text(json.dumps(state), encoding="utf-8")
if out:
    sys.stdout.buffer.write(out)
if err:
    sys.stderr.write(err)
sys.exit(rc)
"""


def run_release_subprocess_e2e(
    tmp_path: Path,
    *,
    tag: str = "v1.2.3",
    candidate: str = "",
    sha: str = DEFAULT_COMMIT,
    run_id: str = DEFAULT_RUN_ID,
    releases: list[dict[str, Any]] | None = None,
    notes: bytes = b"the notes\n",
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Execute the real `bash -e` + `python3 -I` step in a clean isolated subprocess."""
    from ci_shell import SYSTEM, run_step

    release_dir = tmp_path / "release"
    populate_release_dir(release_dir, tag, notes=notes)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh_bin = bin_dir / "gh"
    gh_bin.write_text(SUBPROCESS_GH, encoding="utf-8")
    gh_bin.chmod(0o755)
    sleep_bin = bin_dir / "sleep"
    sleep_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep_bin.chmod(0o755)

    serialized = []
    for r in releases or []:
        serialized.append(
            {
                **r,
                "assets": [
                    {**{k: v for k, v in a.items() if k != "_bytes"}, "_hex": a["_bytes"].hex()}
                    for a in r.get("assets", [])
                ],
            }
        )
    hub_state = tmp_path / "hub_state.json"
    hub_state.write_text(
        json.dumps(
            {
                "tag_sha": sha,
                "releases": serialized,
                "next_id": 88,
                "calls": [],
                "writes": [],
            }
        ),
        encoding="utf-8",
    )

    wf = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    step = next(s for s in wf["jobs"]["release"]["steps"] if s.get("name") == "Create the release")
    done = run_step(
        step["run"],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:{SYSTEM}:/opt/homebrew/bin:/usr/local/bin",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_REF_NAME": tag,
            "GITHUB_SHA": sha,
            "GITHUB_RUN_ID": run_id,
            "GH_REPO": "owner/repo",
            "CANDIDATE": candidate,
            "HUB_STATE": str(hub_state),
        },
    )
    return done, json.loads(hub_state.read_text(encoding="utf-8"))
