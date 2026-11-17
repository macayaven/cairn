"""A reference connector for protocol 1: the Markdown files of one folder, served as items.

Copy it and change `items()` to read your own system; the rest is the protocol
(docs/connector-protocol.md). Standard library only.

    export CONNECTOR_SECRET=...   # made up by you, given to nobody else
    python connector.py --folder notes --port 8811 --secret-env CONNECTOR_SECRET

- Each `*.md` file of the folder (not its subfolders) is an item: its name is the `id`, its
  contents the `text`, its modification time the `at`. Items come oldest first (by that
  time, then by name), so a file written later comes after the ones before it.
- The cursor is the time and name of the last item given: the next answer starts after
  it. The same request always gets the same answer, and a file added later comes after.
- A file edited keeps its name, so it is the same item: the memory it goes to writes an
  item once, by its id, and does not write the edit. Save a new version under a new name.
- The secret comes from the environment, is compared in constant time, and is never
  printed or logged. It listens on 127.0.0.1 unless --host says otherwise: put it behind
  HTTPS (a reverse proxy) before anything else on the network reaches it.
"""

import argparse
import hmac
import json
import os
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROTOCOL = 1
MAX_REQUEST = 64 * 1024  # bytes of a request's body
MAX_ANSWER = 3 * 1024 * 1024  # bytes of items in one answer, under the protocol's 4 MiB
MAX_LIMIT = 200
MAX_ID = 200  # characters of an item's id


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def items(folder: Path, since: datetime) -> list[tuple[tuple[int, str], Path]]:
    """Every item from `since` on, oldest first: (its place in the order, its file).
    Change this to read your own system: an item needs a stable id and a place in an
    order that does not change."""
    found = []
    for path in folder.glob("*.md"):
        if not path.is_file() or len(path.name) > MAX_ID:  # its name is its id: 200 at most
            continue
        modified = path.stat().st_mtime_ns
        if datetime.fromtimestamp(modified / 1e9, UTC) >= since:
            found.append(((modified, path.name), path))
    return sorted(found)


def cursor_of(place: tuple[int, str]) -> str:
    return f"{place[0]}:{place[1]}"


def place_of(cursor: str) -> tuple[int, str] | None:
    modified, _, name = cursor.partition(":")
    return (int(modified), name) if modified.isdigit() and name else None


def answer(folder: Path, request: dict) -> tuple[int, dict]:
    """The status and body for one request of protocol 1."""
    since = parse_time(request.get("since"))
    cursor, limit = request.get("cursor"), request.get("limit")
    if request.get("protocol") != PROTOCOL or since is None:
        return 400, {"error": "this connector speaks protocol 1: send protocol and since"}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        return 400, {"error": "limit is a whole number from 1 to 200"}
    after = None
    if cursor is not None:
        after = place_of(cursor) if isinstance(cursor, str) else None
        if after is None:
            return 400, {"error": "the cursor is not one this connector gave"}
    waiting = [(place, path) for place, path in items(folder, since) if not after or place > after]
    given, size, last = [], 0, after
    for place, path in waiting:
        if len(given) == limit:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():  # the protocol's text is never blank: an empty file is no item
            last = place
            continue
        item = {
            "id": path.name,
            "text": text,
            "title": path.stem[:200],
            "at": stamp(datetime.fromtimestamp(place[0] / 1e9, UTC)),
        }
        cost = len(json.dumps(item).encode())
        if given and size + cost > MAX_ANSWER:
            break
        if cost > MAX_ANSWER:  # one file too large for any answer: passed over
            last = place
            continue
        given.append(item)
        size, last = size + cost, place
    more = bool(waiting) and last != waiting[-1][0]
    return 200, {
        "protocol": PROTOCOL,
        "items": given,
        "cursor": cursor_of(last) if last else cursor,
        "more": more,
    }


def handler(folder: Path, secret: str) -> type[BaseHTTPRequestHandler]:
    expected = f"Bearer {secret}".encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            """One line per request, without its headers: the secret is never logged."""
            status = args[1] if len(args) > 1 else ""
            sys.stderr.write(f"{self.command} {self.path.split('?')[0]} {status}\n")

        def send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            given = (self.headers.get("Authorization") or "").encode()
            if not hmac.compare_digest(given, expected):
                return self.send(401, {"error": "the secret was refused"})
            length = self.headers.get("Content-Length") or ""
            if not length.isdigit() or int(length) > MAX_REQUEST:
                return self.send(413, {"error": "a request of protocol 1 is a small JSON object"})
            try:
                request = json.loads(self.rfile.read(int(length)))
            except ValueError:
                request = None
            if not isinstance(request, dict):
                return self.send(400, {"error": "the request is not a JSON object"})
            self.send(*answer(folder, request))

        def do_GET(self) -> None:
            self.send(405, {"error": "this connector answers POST only"})

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve a folder's Markdown files by protocol 1.")
    parser.add_argument("--folder", type=Path, required=True, help="the folder of *.md files")
    parser.add_argument("--port", type=int, default=8811, help="the port (8811)")
    parser.add_argument("--host", default="127.0.0.1", help="the address to bind (127.0.0.1)")
    parser.add_argument("--secret-env", required=True, help="the variable that holds the secret")
    args = parser.parse_args()
    secret = (os.environ.get(args.secret_env) or "").strip()
    if not secret:
        parser.error(f"set {args.secret_env} in the environment: it holds the secret")
    if not args.folder.is_dir():
        parser.error(f"{args.folder} is not a folder")
    server = ThreadingHTTPServer((args.host, args.port), handler(args.folder, secret))
    sys.stderr.write(f"serving {args.folder} by protocol 1 at http://{args.host}:{args.port}/\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
