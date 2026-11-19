"""Smoke-test a cairn container image: python3 scripts/smoke_container.py IMAGE

IMAGE must already be local (build or pull it first): the script never pulls. It runs the
image the way the README tells people to and checks it over HTTP from the host:
health, the non-root user, auth on the API, MCP and UI, a key minted into the volume and
copied out, a note-then-recall round trip (which proves FTS5 in the image's SQLite), MCP over
HTTP, the Host check on /mcp, a browser-style login, data that outlives the container, an
export taken on the command line, a server run from a copy of the stopped container's data,
a bind mount written as the caller's own user, an SQLite new enough to overwrite what the
search index drops, that export imported into a fresh install by the run guide's own steps,
the two typefaces and a licence served by the image as files of their real size and
type, and the project's own licence, MIT, in the package's metadata and at /app/LICENSE, the
same bytes as the repository's LICENSE. Standard library only, so it runs on a CI
runner or any host with Docker and Python 3.10 or later. Exit code 0 when every check passes.

Isolation, resources and cleanup: every container is named cairn-smoke-<pid>-<n> and labelled
cairn.smoke=<pid>, and gets at most 512 MiB of memory, one CPU and 256 processes, no GPU, and
no published port except an ephemeral one on 127.0.0.1. The containers, the named volume and
the temporary directory are removed at the end, pass or fail, and the last check proves that
none of them is left. The API key lives only in a 0600 file inside that temporary directory
and is never printed.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

PORT = 8765
FOREIGN_HOST = "cairn.example.test"
LIMITS = ("--memory", "512m", "--cpus", "1", "--pids-limit", "256")
WORD = "quartzlantern"  # a word no fixture uses, so a hit can only be our note
PROTOCOL = "2025-06-18"
# Files the pages need from /static/fonts/, served without a session: (bytes, media type).
FONT_FILES = {
    "atkinson-hyperlegible-next.ttf": (114_552, "font/ttf"),
    "atkinson-hyperlegible-mono.ttf": (53_960, "font/ttf"),
    "atkinson-hyperlegible-next.OFL.txt": (4_431, "text/plain"),
}

ROOT = Path(__file__).resolve().parent.parent
# Run inside the container: what importlib.metadata reports for the distribution, and the
# plain copy at /app, as {name: sha256}. Prints one JSON line.
LICENCE_PROBE = """
import hashlib, importlib.metadata as md, json, pathlib
dist = md.distribution("cairn-memory")
metadata = {}
for f in dist.files or ():
    # a path like cairn_memory-0.0.1.dist-info/licenses/LICENSE
    if f.parts[0].endswith(".dist-info") and f.parts[1:2] == ("licenses",):
        metadata["/".join(f.parts[2:])] = hashlib.sha256(f.read_binary()).hexdigest()
plain = {}
if pathlib.Path("/app/LICENSE").is_file():
    plain["LICENSE"] = hashlib.sha256(pathlib.Path("/app/LICENSE").read_bytes()).hexdigest()
print(json.dumps({"expression": dist.metadata["License-Expression"],
                  "metadata": metadata, "plain": plain}))
"""


def fts5_secure_delete() -> dict:
    """Whether this Python's SQLite gives FTS5 secure-delete: cairn's own flag (the one
    tests/unit/test_erasure_unit.py skips on) and whether an FTS5 table takes the option.
    Runs inside the image as SECURE_DELETE_PROBE; the unit tests run it on the host too."""
    import sqlite3

    from cairn.store.schema import _FTS5_SECURE_DELETE

    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        con.execute("INSERT INTO probe (probe, rank) VALUES ('secure-delete', 1)")
        taken = True
    except sqlite3.Error:
        taken = False
    finally:
        con.close()
    return {"sqlite": sqlite3.sqlite_version, "cairn": _FTS5_SECURE_DELETE, "fts5": taken}


# The function's own source and one JSON line of what it returns, for `python -c`.
SECURE_DELETE_PROBE = (
    "import json\n"
    + inspect.getsource(fts5_secure_delete)
    + "print(json.dumps(fts5_secure_delete()))\n"
)


# Seconds a container gets to report healthy. Natively it takes a few seconds; under QEMU
# emulation in CI it can take over a minute, so CI raises this for the emulated platform.
HEALTH_TIMEOUT = float(os.environ.get("CAIRN_SMOKE_HEALTH_TIMEOUT", "90"))


class Failed(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # report a 3xx, don't follow
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http(
    method: str, url: str, *, headers: dict[str, str] | None = None, body: Any = None
) -> tuple[int, Any, bytes]:
    """(status, headers, body) of one request. HTTP error statuses are returned, not raised."""
    headers = dict(headers or {})
    data = None
    if isinstance(body, dict):
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        data = body.encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=15) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise Failed(f"docker {args[0]} exited {result.returncode}: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise Failed(message)


class Smoke:
    def __init__(self, image: str) -> None:
        self.image = image
        self.tag = str(os.getpid())
        self.volume = f"cairn-smoke-{self.tag}"
        self.volumes = [self.volume]
        self.names: list[str] = []
        self.tmp = Path(tempfile.mkdtemp(prefix="cairn-smoke-"))
        self.key = ""
        self.base = ""

    # -- containers ---------------------------------------------------------------------------

    def start(self, mount: str, *command: str, user: str | None = None) -> str:
        name = f"cairn-smoke-{self.tag}-{len(self.names) + 1}"
        self.names.append(name)
        args = ["run", "-d", "--pull", "never", "--name", name, *LIMITS]  # test this image only
        args += ["--label", f"cairn.smoke={self.tag}", "--health-interval", "2s"]
        args += ["-p", f"127.0.0.1::{PORT}", "-v", mount]
        if user:
            args += ["--user", user]
        docker(*args, self.image, *command)
        first = docker("port", name, f"{PORT}/tcp").splitlines()[0]
        self.base = "http://" + first.replace("0.0.0.0", "127.0.0.1")
        self.wait_healthy(name)
        return name

    def wait_healthy(self, name: str, timeout: float = HEALTH_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        state = ""
        while time.monotonic() < deadline:
            state = docker(
                "inspect",
                "-f",
                "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                name,
            )
            if state.split()[0] in ("exited", "dead"):
                logs = subprocess.run(
                    ["docker", "logs", "--tail", "15", name], capture_output=True, text=True
                )
                raise Failed(f"{name} stopped: {(logs.stdout + logs.stderr).strip()[-800:]}")
            if state == "running healthy":
                status, _, _ = http("GET", f"{self.base}/healthz")
                expect(status == 200, f"/healthz answered {status} on a healthy container")
                return
            time.sleep(1)
        # Say which side is at fault: the server (no answer from outside either) or the probe.
        try:
            outside = f"/healthz from the host answered {http('GET', f'{self.base}/healthz')[0]}"
        except OSError as error:
            outside = f"/healthz from the host failed ({error})"
        probe = subprocess.run(
            ["docker", "inspect", "-f", "{{range .State.Health.Log}}{{.ExitCode}} {{end}}", name],
            capture_output=True,
            text=True,
        ).stdout.split()
        raise Failed(
            f"{name} not healthy after {timeout:.0f}s (state: {state!r}; {outside};"
            f" last probe exit codes: {' '.join(probe[-5:]) or 'none'})"
        )

    # -- helpers --------------------------------------------------------------------------------

    def api(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, _, raw = http(method, self.base + path, headers={"x-api-key": self.key}, body=body)
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None

    def mcp(self, method: str, params: dict | None = None, host: str | None = None) -> Any:
        headers = {
            "x-api-key": self.key,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL,
        }
        if host:
            headers["Host"] = host
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            message["params"] = params
        status, _, raw = http("POST", f"{self.base}/mcp", headers=headers, body=message)
        return status, (json.loads(raw) if status == 200 else None)

    def initialize(self, host: str | None = None) -> tuple[int, Any]:
        return self.mcp(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "cairn-smoke", "version": "0"},
            },
            host=host,
        )

    def recall_hits(self) -> list[dict]:
        status, found = self.api("GET", f"/api/search?q={WORD}&project=smoke")
        expect(status == 200, f"search answered {status}")
        return [hit for hit in found["hits"] if WORD in hit["text"]]

    # -- checks -------------------------------------------------------------------------------

    def starts_healthy(self) -> str:
        self.start(f"{self.volume}:/data")
        return "a named volume on /data; Docker health is healthy and /healthz is 200"

    def runs_as_non_root(self) -> str:
        uid = docker("exec", self.names[-1], "id", "-u")
        expect(uid != "0", "the server runs as root")
        return f"uid {uid}"

    def refuses_without_credentials(self) -> str:
        status, _, _ = http("GET", f"{self.base}/api/projects")
        expect(status == 401, f"GET /api/projects without a key answered {status}")
        status, _, _ = http("POST", f"{self.base}/mcp", body={})
        expect(status == 401, f"POST /mcp without a key answered {status}")
        status, headers, _ = http("GET", f"{self.base}/")
        expect(status == 303 and headers.get("Location") == "/login", f"UI answered {status}")
        return "API 401, MCP 401, UI 303 to /login"

    def mints_a_key_into_the_volume(self) -> str:
        name, inside, outside = self.names[-1], "/data/smoke.env", self.tmp / "smoke.env"
        docker(
            "exec", name, "python", "-m", "cairn.server", "key-create",
            "--db", "/data/cairn.db", "--label", "smoke", "--out", inside,
        )  # fmt: skip
        docker("cp", f"{name}:{inside}", str(outside))
        docker("exec", name, "rm", inside)
        info = outside.stat()
        mode = stat.S_IMODE(info.st_mode)
        expect(mode == 0o600, f"the copied key file has mode {mode:o}, not 600")
        expect(info.st_uid == os.getuid(), "the copied key file is not owned by the caller")
        lines = outside.read_text(encoding="utf-8").splitlines()
        values = [line.split("=", 1)[1] for line in lines if line.startswith("CAIRN_API_KEY=")]
        expect(len(values) == 1 and values[0], "the key file has no CAIRN_API_KEY line")
        self.key = values[0]
        return "key-create in the container, docker cp out: mode 600, owned by the caller"

    def notes_and_recalls_over_rest(self) -> str:
        status, created = self.api(
            "POST", "/api/projects", {"name": "smoke", "description": "container smoke test"}
        )
        expect(status == 201, f"creating a project answered {status}")
        text = f"# Harbour lantern\n\nThe {WORD} keeps the harbour tide tables.\n"
        status, noted = self.api(
            "POST", "/api/projects/smoke/notes", {"text": text, "source_ref": "smoke:container"}
        )
        expect(status == 201, f"the note answered {status}")
        expect(noted["status"] == "saved", f"the note compiled to {noted['status']!r}")
        hits = self.recall_hits()
        expect(len(hits) == 1, f"search found {len(hits)} hits for the note")
        return f"project {created['name']!r}, note saved as page {noted['page']!r}, 1 hit"

    def answers_mcp(self) -> str:
        status, reply = self.initialize()
        expect(status == 200, f"initialize answered {status}")
        server = reply["result"]["serverInfo"]["name"]
        expect(server == "cairn", f"serverInfo.name is {server!r}")
        status, reply = self.mcp("tools/list")
        expect(status == 200, f"tools/list answered {status}")
        tools = sorted(tool["name"] for tool in reply["result"]["tools"])
        status, reply = self.mcp(
            "tools/call", {"name": "recall", "arguments": {"query": WORD, "project": "smoke"}}
        )
        expect(status == 200 and not reply["result"].get("isError"), "recall failed")
        hits = json.loads(reply["result"]["content"][0]["text"])["hits"]
        expect(len(hits) == 1, f"recall over MCP found {len(hits)} hits")
        return f"{len(tools)} tools ({', '.join(tools)}); recall found the note"

    def refuses_a_foreign_host_on_mcp(self) -> str:
        status, _ = self.initialize(host=FOREIGN_HOST)
        expect(status == 421, f"Host {FOREIGN_HOST} answered {status}, not 421")
        return f"Host {FOREIGN_HOST} gets 421 without --allowed-host"

    def logs_in_like_a_browser(self) -> str:
        browser = {"Origin": self.base, "Sec-Fetch-Site": "same-origin"}
        form = urllib.parse.urlencode({"key": self.key})
        status, headers, _ = http("POST", f"{self.base}/login", headers=browser, body=form)
        expect(status == 303 and headers.get("Location") == "/", f"login answered {status}")
        cookie = headers.get("Set-Cookie", "")
        flags = cookie.lower()
        expect("httponly" in flags and "samesite=strict" in flags, "cookie flags are missing")
        session = {"Cookie": cookie.split(";", 1)[0]}
        status, _, page = http("GET", f"{self.base}/", headers=session)
        expect(status == 200 and b"smoke" in page, f"the home page answered {status}")
        status, _, _ = http("POST", f"{self.base}/logout", headers={**session, **browser})
        expect(status == 303, f"logout answered {status}")
        status, _, _ = http("GET", f"{self.base}/", headers=session)
        expect(status == 303, "the session still works after logout")
        return "303 with an HttpOnly SameSite=Strict cookie; home 200; logout ends the session"

    def keeps_data_in_a_new_container(self) -> str:
        docker("rm", "-f", self.names[-1])
        self.start(
            f"{self.volume}:/data",
            "serve", "--db", "/data/cairn.db", "--host", "0.0.0.0",
            "--allowed-host", FOREIGN_HOST,
        )  # fmt: skip
        expect(len(self.recall_hits()) == 1, "the note did not survive the new container")
        status, _ = self.initialize(host=FOREIGN_HOST)
        expect(status == 200, f"Host {FOREIGN_HOST} answered {status} with --allowed-host")
        return f"same volume, new container: key and note intact; {FOREIGN_HOST} now 200"

    def exports_from_the_command_line(self) -> str:
        name, inside, outside = self.names[-1], "/data/export.zip", self.tmp / "export.zip"
        docker(
            "exec", name, "python", "-m", "cairn.server", "export",
            "--db", "/data/cairn.db", "--account", "acct_default", "--out", inside,
        )  # fmt: skip
        docker("cp", f"{name}:{inside}", str(outside))
        docker("exec", name, "rm", inside)
        with zipfile.ZipFile(outside) as archive:
            names = archive.namelist()
            notes = [
                n for n in names if n.startswith("projects/smoke/notes/") and n.endswith(".md")
            ]
            expect("export.json" in names, "the export has no export.json")
            expect(len(notes) == 1, f"the export holds {len(notes)} notes of the smoke project")
            expect(WORD in archive.read(notes[0]).decode("utf-8"), "the note is not as written")
        return f"export in the container, docker cp out: {len(names)} files, the note as written"

    def runs_from_a_copy_of_the_database(self) -> str:
        """The run guide's whole backup: stop, copy /data out, start a server on the copy."""
        copy = self.tmp / "copy"
        docker("stop", self.names[-1])
        docker("cp", f"{self.names[-1]}:/data", str(copy))
        expect((copy / "cairn.db").is_file(), "the copy holds no cairn.db")
        self.start(f"{copy}:/data", user=f"{os.getuid()}:{os.getgid()}")
        expect(len(self.recall_hits()) == 1, "the note is not in the server run from the copy")
        return "docker stop, docker cp /data out, a new container on the copy: key and note intact"

    def writes_a_bind_mount_as_the_caller(self) -> str:
        bind = self.tmp / "bind"
        bind.mkdir()
        self.start(f"{bind}:/data", user=f"{os.getuid()}:{os.getgid()}")
        db = bind / "cairn.db"
        expect(db.is_file(), "no cairn.db in the bind mount")
        expect(db.stat().st_uid == os.getuid(), "cairn.db is not owned by the caller")
        return f"--user {os.getuid()}:{os.getgid()} on a host directory: cairn.db is the caller's"

    def overwrites_what_the_index_drops(self) -> str:
        """The privacy guide says a deleted note's text is overwritten in the search index at
        once. cairn asks FTS5 for that (secure-delete), which SQLite has from 3.42. The image
        is asked with cairn's own flag, the one tests/unit/test_erasure_unit.py skips on, and
        with an FTS5 table that must take the option."""
        out = docker("exec", self.names[-1], "python", "-c", SECURE_DELETE_PROBE)
        try:
            found = json.loads(out)
        except ValueError:
            raise Failed(f"the secure-delete probe printed no JSON: {out[:200]!r}") from None
        version = found.get("sqlite")
        expect(
            found.get("cairn") is True and found.get("fts5") is True,
            f"The image's SQLite {version} does not give FTS5 secure-delete, so a deleted"
            f" note's text would stay in the search index (cairn's flag: {found.get('cairn')},"
            f" FTS5 took the option: {found.get('fts5')}).",
        )
        return f"SQLite {version} in the image: FTS5 takes secure-delete, as cairn's flag says"

    def imports_an_export_as_the_run_guide_says(self) -> str:
        """A fresh install on its own volume takes the export taken above, by the run guide's
        steps: written in as cairn's own user, imported, removed."""
        export, inside = self.tmp / "export.zip", "/data/export.zip"
        expect(export.is_file(), "no export to import: the export check did not pass")
        volume = f"{self.volume}-import"
        self.volumes.append(volume)
        name = self.start(f"{volume}:/data")
        # Why the guide does not say `docker cp`: the file would be root's, mode 600.
        docker("cp", str(export), f"{name}:/data/copied.zip")
        unreadable = subprocess.run(
            ["docker", "exec", name, "python", "-c", "open('/data/copied.zip', 'rb')"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        expect(
            unreadable.returncode != 0,
            "cairn can read a file put in with docker cp: the run guide says it cannot",
        )
        docker("exec", name, "rm", "-f", "/data/copied.zip")  # the directory is cairn's
        with export.open("rb") as archive:
            written = subprocess.run(
                ["docker", "exec", "-i", name, "sh", "-c", f"cat > {inside}"],
                stdin=archive, capture_output=True, text=True, check=False,
            )  # fmt: skip
        expect(written.returncode == 0, f"writing the export in failed: {written.stderr[-200:]}")
        docker(
            "exec", name, "python", "-m", "cairn.server", "import",
            "--db", "/data/cairn.db", "--account", "acct_default", "--in", inside,
        )  # fmt: skip
        docker("exec", name, "rm", inside)
        made = "/data/import.env"
        docker(
            "exec", name, "python", "-m", "cairn.server", "key-create",
            "--db", "/data/cairn.db", "--label", "import", "--out", made,
        )  # fmt: skip
        line = docker("exec", name, "cat", made)
        docker("exec", name, "rm", made)
        self.key = line.split("=", 1)[1].strip()
        expect(len(self.recall_hits()) == 1, "the imported note is not found")
        return "a fresh install: the export written in as cairn's user, imported, the note found"

    def serves_the_typefaces(self) -> str:
        """The stylesheet's two fonts and a licence are in the image and served, no session."""
        for name, (size, kind) in FONT_FILES.items():
            status, headers, body = http("GET", f"{self.base}/static/fonts/{name}")
            expect(status == 200, f"/static/fonts/{name} answered {status}")
            served = headers.get("Content-Type", "")
            expect(served.split(";")[0] == kind, f"{name} is served as {served!r}, not {kind}")
            expect(len(body) == size, f"{name} is {len(body)} bytes, not {size}")
        return ", ".join(f"{name} {size} bytes" for name, (size, _) in FONT_FILES.items())

    def carries_the_licence(self) -> str:
        """The MIT notice travels in the image: in the installed package's metadata (the
        licence expression and the one file under its licenses folder) and at /app/LICENSE,
        the same bytes as the repository's LICENSE."""
        out = docker("exec", self.names[-1], "python", "-c", LICENCE_PROBE)
        try:
            found = json.loads(out)
        except ValueError:
            raise Failed(f"the licence probe printed no JSON: {out[:200]!r}") from None
        expect(
            found.get("expression") == "MIT",
            f"License-Expression is {found.get('expression')!r}, not 'MIT'",
        )
        want = hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest()
        for place, where in (("metadata", "the package's metadata"), ("plain", "/app/LICENSE")):
            files = found.get(place) or {}
            got = files.get("LICENSE")
            expect(got is not None, f"LICENSE is missing from {where}")
            expect(got == want, f"LICENSE in {where} is not the repository's LICENSE")
            extra = sorted(set(files) - {"LICENSE"})
            expect(not extra, f"{where} holds another licence file: {', '.join(extra)}")
        return "License-Expression MIT; LICENSE in the metadata and at /app, same bytes"

    def cleanup(self) -> str:
        for name in self.names:
            docker("rm", "-f", name, check=False)
        for volume in self.volumes:
            docker("volume", "rm", "-f", volume, check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)
        left = docker("ps", "-aq", "--filter", f"label=cairn.smoke={self.tag}", check=False)
        volumes = docker("volume", "ls", "-q", "--filter", f"name={self.volume}", check=False)
        expect(not left and not volumes and not self.tmp.exists(), "smoke resources remain")
        return "no container, volume or temporary directory left"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1].startswith("-"):
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    smoke = Smoke(argv[1])
    checks: list[Callable[[], str]] = [
        smoke.starts_healthy,
        smoke.runs_as_non_root,
        smoke.refuses_without_credentials,
        smoke.mints_a_key_into_the_volume,
        smoke.notes_and_recalls_over_rest,
        smoke.answers_mcp,
        smoke.refuses_a_foreign_host_on_mcp,
        smoke.logs_in_like_a_browser,
        smoke.keeps_data_in_a_new_container,
        smoke.exports_from_the_command_line,
        smoke.runs_from_a_copy_of_the_database,
        smoke.writes_a_bind_mount_as_the_caller,
        smoke.overwrites_what_the_index_drops,
        smoke.imports_an_export_as_the_run_guide_says,
        smoke.serves_the_typefaces,
        smoke.carries_the_licence,
    ]
    passed = 0
    try:
        for check in checks:
            started = time.monotonic()
            try:
                detail = check()
            except Failed as failure:
                print(f"FAIL {check.__name__}: {failure}")
                break
            print(f"PASS {check.__name__} ({time.monotonic() - started:.1f}s): {detail}")
            passed += 1
    finally:
        try:
            print(f"PASS cleanup: {smoke.cleanup()}")
        except Failed as failure:
            print(f"FAIL cleanup: {failure}")
            passed = -1
    print(f"{max(passed, 0)}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
