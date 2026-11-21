"""`connector-check` (C9): a connector of your own held to protocol 1 before a source reads it.

Contract (frozen by tests/acceptance/test_c9_connector.py; pinned beside it by
tests/unit/test_connector_check_unit.py; the protocol is docs/connector-protocol.md). It
shares the kind's reading of the protocol (cairn.sources.connector: check_url, ask, parsed,
examine), never a copy of it, so the checker and the kind cannot disagree about a rule.

The command
- `python -m cairn.server connector-check --url URL --secret-env NAME [--since TIME]
  [--limit N]`. It opens no database, takes no --db and writes no file. It talks only to
  --url (by the kind's transport rule: check_url), with the secret read from the
  environment variable --secret-env names, sent only as the Bearer of the requests the
  kind would send. --since is RFC 3339 with its zone (LOOK_BACK, 30 days, before now when
  left out: a connector asked from now on would give nothing to check); --limit is 1 to 200
  (50).
- Exit 0 when every rule holds, 1 when one fails, 2 for bad options (a missing or bad
  option, a url the transport rule refuses) and for a secret variable that is unset or
  blank, with nothing sent.

The conversation, as a run would have it, then one more request
1. The first request (cursor null), and 2. the same request again; then 3. the walk: the
   first answer's cursor, and on while an answer says `more`, at most FOLLOWED (20, the
   most a run asks for) answers counting the first. A connector with a longer backlog is
   not at fault: the line for moving on says how far it followed. The walk stops at an
   answer that breaks the protocol as a whole, does not come, or does not move on.
4. One request (cursor null) with a secret made up for it (made_up: new each time, never
   holding the real one).
Every request has its own deadline, ANSWER_SECONDS (the sources' per-request timeout) from
when it starts, as cairn.sources.timing applies it.

What it prints
- On stdout one line per rule and nothing else, in the order of RULES whatever happens:
  "ok" or "FAILED", the rule's title (TITLES), and what it saw: a FAILED line the first
  breach by the number of the answer (in the order asked) and an item by its id (shown_id:
  one line, never the secret), and how many more. Never the secret, never an item's text,
  never anything of an error answer's body but its status.
- A rule it could not check, because no answer of protocol 1 came, is FAILED as "not
  checked": a check that did not happen is not a pass.
- On stderr, one line: how many rules failed, or that every rule holds.
"""

import argparse
import asyncio
import json
import os
import secrets as tokens
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from cairn.sources import SourceError
from cairn.sources import connector as c
from cairn.sources.config import ENV_NAME
from cairn.sources.runner import SOURCE_TIMEOUT, source_client

COMMAND = "connector-check"
FOLLOWED = c.ANSWERS_PER_RUN  # answers the walk follows at most, counting the first
ANSWER_SECONDS = SOURCE_TIMEOUT  # each request's deadline
LOOK_BACK = timedelta(days=30)  # --since, when left out, is this long before now
REPEAT = "same request twice"
WRONG_SECRET = "wrong secret"
DEADLINE = "deadline"
RULES = (
    c.SHAPE,
    c.UNIQUE,
    c.BOUNDS,
    c.SINCE,
    REPEAT,
    c.MOVES_ON,
    c.CURSOR,
    WRONG_SECRET,
    DEADLINE,
)
TITLES = {
    c.SHAPE: "the answer's shape and protocol",
    c.UNIQUE: "ids unique within an answer",
    c.BOUNDS: "every item within its bounds",
    c.SINCE: "no item's at before since",
    REPEAT: "the same request twice gives the same items first",
    c.MOVES_ON: "every answer with more moves on",
    c.CURSOR: "the cursor's size, and no secret in it",
    WRONG_SECRET: "a wrong secret is refused (401 or 403, no items)",
    DEADLINE: "answers within the deadline",
}
NOT_CHECKED = "not checked: no answer of protocol 1 came to check it on"


@dataclass(frozen=True)
class Verdict:
    """One rule's outcome: whether it holds, and what was seen."""

    rule: str
    ok: bool
    said: str


def line(verdict: Verdict) -> str:
    """The line printed for a verdict."""
    return f"{'ok' if verdict.ok else 'FAILED'} {TITLES[verdict.rule]}: {verdict.said}"


def made_up(secret: str) -> str:
    """A secret for the request that must be refused: new each time, and neither holding
    the real one nor held in it."""
    while True:
        key = "connector-check-wrong-" + tokens.token_urlsafe(24)
        if secret not in key and key not in secret:
            return key


def default_since(clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> datetime:
    return clock() - LOOK_BACK


def _answers(count: int) -> str:
    return f"{count} answer{'s' if count != 1 else ''}"


def _items_in(data: bytes) -> int:
    """How many items an error answer's body holds (0 when it is no such JSON)."""
    try:
        items = json.loads(data).get("items")
    except (ValueError, RecursionError, AttributeError):
        return 0
    return len(items) if isinstance(items, list) else 0


class _Conversation:
    """The requests of one check and what each rule found (see the module docstring)."""

    def __init__(self, http, url, secret, since, limit, clock) -> None:
        self.http, self.url, self.secret = http, url, secret
        self.since, self.limit, self.clock = since, limit, clock
        self.secrets = (secret, f"Bearer {secret}")
        self.found: dict[str, list[str]] = {rule: [] for rule in RULES}
        self.number = 0  # requests sent
        self.read = 0  # answers of protocol 1 read
        self.items = 0
        self.late: list[int] = []
        self.slowest = 0.0

    async def asking(self, cursor: str | None, key: str, rule: str) -> c.Reply | None:
        """One request; None when it did not come in time (`late`) or failed (a finding of
        `rule`)."""
        self.number += 1
        began = time.monotonic()
        try:
            return await c.ask(
                self.http, self.url, key, c.request_body(self.since, cursor, self.limit),
                deadline=self.clock() + timedelta(seconds=ANSWER_SECONDS), clock=self.clock,
                secrets=self.secrets,
            )  # fmt: skip
        except c.Late:
            self.late.append(self.number)
        except SourceError as error:  # the kind's words: never a body or the secret
            self.found[rule].append(f"answer {self.number}: {error}")
        finally:
            self.slowest = max(self.slowest, time.monotonic() - began)
        return None

    async def answer(self, cursor: str | None) -> c.Answer | None:
        """An answer of protocol 1 to `cursor`, its findings kept; None when there is none."""
        reply = await self.asking(cursor, self.secret, c.SHAPE)
        if reply is None:
            return None
        number = self.number
        if reply.status != 200:
            self.found[c.SHAPE].append(f"answer {number}: HTTP {reply.status}, where 200 was due")
            return None
        data, broken = c.parsed(reply)
        got = (
            c.Answer(findings=[broken])
            if broken
            else c.examine(
                data, sent=cursor, limit=self.limit, since=self.since, secrets=self.secrets
            )
        )
        for finding in got.findings:
            self.found[finding.rule].append(f"answer {number}: {finding.said}")
        if any(finding.rule == c.SHAPE for finding in got.findings):
            return None
        self.read += 1
        self.items += len(got.items)
        return got

    async def run(self) -> list[Verdict]:
        first = await self.answer(None)
        second = await self.answer(None) if first is not None else None
        repeat = self._compare(first, second)
        walk = await self._walk(first)
        wrong = await self._wrong_secret()
        return self._verdicts(repeat, walk, wrong)

    def _compare(self, first: c.Answer | None, second: c.Answer | None) -> Verdict:
        if first is None:
            return Verdict(REPEAT, False, NOT_CHECKED)
        if second is None:
            return Verdict(REPEAT, False, "answer 2: the second asking gave no answer to compare")
        if first.more:
            if second.ids != first.ids or (second.cursor, second.more) != (first.cursor, True):
                return Verdict(
                    REPEAT,
                    False,
                    "answer 2: the first answer said more, so asking again must give it exactly"
                    f" (the same ids in order, the same cursor): {self._difference(first, second)}",
                )
            return Verdict(REPEAT, True, "asked twice, the first answer came back exactly")
        if second.ids[: len(first.ids)] != first.ids:
            return Verdict(
                REPEAT,
                False,
                "answer 2: asking again must give the first answer's ids first, in order:"
                f" {self._difference(first, second)}",
            )
        grew = len(second.ids) - len(first.ids)
        return Verdict(
            REPEAT,
            True,
            "asked twice, the first answer's ids came back first, in order"
            + (f", and {grew} newer after them" if grew else ""),
        )

    def _difference(self, first: c.Answer, second: c.Answer) -> str:
        for index, (one, two) in enumerate(zip(first.ids, second.ids, strict=False)):
            if one != two:
                was, now = self._id(one), self._id(two)
                return f"item {index + 1} was {was} and is {now}"
        if len(second.ids) < len(first.ids):
            return f"{len(first.ids)} items the first time, {len(second.ids)} the second"
        return "another cursor, or another more"

    def _id(self, value: str | None) -> str:
        return "(no id)" if value is None else c.shown_id(value, self.secrets)

    async def _walk(self, first: c.Answer | None) -> Verdict:
        if first is None:
            return Verdict(c.MOVES_ON, False, NOT_CHECKED)
        followed, current = 1, first
        while current.more and not self.found[c.MOVES_ON] and followed < FOLLOWED:
            following = await self.answer(current.cursor)
            if following is None:
                stopped = "the walk stopped where an answer did not come or broke the protocol"
                return self._moved(f"followed {_answers(followed)}; {stopped}")
            followed, current = followed + 1, following
        if current.more and not self.found[c.MOVES_ON]:
            return self._moved(
                f"followed {_answers(followed)}, the most a run asks for, and it still had more:"
                " not a fault, since a run goes on from there next time"
            )
        return self._moved(f"followed {_answers(followed)} to the end")

    def _moved(self, said: str) -> Verdict:
        if self.found[c.MOVES_ON]:
            return Verdict(c.MOVES_ON, False, self._first(c.MOVES_ON))
        return Verdict(c.MOVES_ON, True, said)

    async def _wrong_secret(self) -> Verdict:
        reply = await self.asking(None, made_up(self.secret), WRONG_SECRET)
        number = self.number
        if self.found[WRONG_SECRET]:
            return Verdict(WRONG_SECRET, False, self._first(WRONG_SECRET))
        if reply is None:
            return Verdict(WRONG_SECRET, False, f"answer {number}: no answer came in time")
        if reply.status not in (401, 403):
            return Verdict(
                WRONG_SECRET,
                False,
                f"answer {number}: a made-up secret was answered HTTP {reply.status}, where 401"
                " or 403 was due",
            )
        held = _items_in(reply.data)
        if held:
            return Verdict(
                WRONG_SECRET,
                False,
                f"answer {number}: refused with HTTP {reply.status}, but with {held} items",
            )
        return Verdict(WRONG_SECRET, True, f"a made-up secret was refused with HTTP {reply.status}")

    def _first(self, rule: str) -> str:
        seen = self.found[rule]
        more = f" (and {len(seen) - 1} more)" if len(seen) > 1 else ""
        return seen[0] + more

    def _verdicts(self, repeat: Verdict, walk: Verdict, wrong: Verdict) -> list[Verdict]:
        def kept(rule: str, ok: str) -> Verdict:
            if self.found[rule]:
                return Verdict(rule, False, self._first(rule))
            if not self.read:
                return Verdict(rule, False, NOT_CHECKED)
            return Verdict(rule, True, ok)

        if self.late:
            deadline = Verdict(
                DEADLINE,
                False,
                f"answer {self.late[0]} did not come within {ANSWER_SECONDS:g} s"
                + (f" (and {len(self.late) - 1} more)" if len(self.late) > 1 else ""),
            )
        else:
            deadline = Verdict(
                DEADLINE,
                True,
                f"the slowest of {self.number} answers took {self.slowest:.1f} s; each may"
                f" take {ANSWER_SECONDS:g} s",
            )
        by_rule = {
            c.SHAPE: kept(c.SHAPE, f"{self.read} answers, each one JSON object of protocol 1"),
            c.UNIQUE: kept(c.UNIQUE, "no id twice in one answer"),
            c.BOUNDS: kept(c.BOUNDS, f"{self.items} items, each within its bounds"),
            c.SINCE: kept(c.SINCE, f"no item dated before {c.stamp(self.since)}"),
            REPEAT: repeat,
            c.MOVES_ON: walk,
            c.CURSOR: kept(
                c.CURSOR, f"every cursor at most {c.CURSOR_MAX:,} characters, none the secret"
            ),
            WRONG_SECRET: wrong,
            DEADLINE: deadline,
        }
        return [by_rule[rule] for rule in RULES]


async def converse(
    http: httpx.AsyncClient,
    url: str,
    secret: str,
    *,
    since: datetime,
    limit: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[Verdict]:
    """The conversation with the connector at `url` (see the module docstring) and a
    verdict for each rule, in the order of RULES."""
    return await _Conversation(http, url, secret, since, limit, clock).run()


# The command


def _url(value: str) -> str:
    try:
        return c.check_url(value)
    except ValueError as error:  # its words never repeat the address
        raise argparse.ArgumentTypeError(str(error)) from None


def _variable(value: str) -> str:
    if not ENV_NAME.fullmatch(value) or c.looks_like_a_credential(value):
        raise argparse.ArgumentTypeError(
            "name the environment variable that holds the secret (capitals, digits and _),"
            " never the secret itself"
        )
    return value


def _since(value: str) -> datetime:
    when = c.moment(value)
    if when is None:
        raise argparse.ArgumentTypeError(
            "a time in RFC 3339 with its zone, such as 2099-01-01T00:00:00Z"
        )
    return when


def _limit(value: str) -> int:
    _, least, most = c.LIMIT
    try:
        asked = int(value)
    except ValueError:
        asked = 0
    if not least <= asked <= most:
        raise argparse.ArgumentTypeError(f"a whole number from {least} to {most}")
    return asked


HELP = (
    "Check a connector of your own against protocol 1 (docs/connector-protocol.md) before a"
    " source reads it. It asks the connector as a run would: the first request, the same"
    " request again, then on while an answer says more, at most"
    f" {FOLLOWED} answers; then one request with a made-up secret, which must be refused."
    " It prints one line per rule on stdout, ok or FAILED, and exits 0 when every rule"
    " holds, 1 when one fails, 2 for a bad option or an unset secret. The secret is read"
    " from the environment variable --secret-env names and sent only to --url, only as the"
    " Bearer; it is never printed, nor is an item's text. It opens no database and writes"
    " nothing."
)


def add_command(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        COMMAND,
        help="check a connector of your own against protocol 1, before a source reads it",
        description=HELP,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--url",
        type=_url,
        required=True,
        help="the connector's address: https, or http to 127.0.0.1 or localhost",
    )
    parser.add_argument(
        "--secret-env",
        type=_variable,
        required=True,
        metavar="NAME",
        help="the environment variable that holds the secret, sent only to --url",
    )
    parser.add_argument(
        "--since",
        type=_since,
        metavar="TIME",
        help="the since sent, RFC 3339 with its zone (30 days ago)",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        default=c.LIMIT[0],
        metavar="N",
        help=f"the limit sent, {c.LIMIT[1]} to {c.LIMIT[2]} ({c.LIMIT[0]})",
    )


def run(args: argparse.Namespace) -> int:
    secret = (os.environ.get(args.secret_env) or "").strip()
    if not secret:
        print(
            f"set {args.secret_env} in this environment: it holds the secret the connector"
            " checks. Nothing was sent.",
            file=sys.stderr,
        )
        return 2
    since = args.since or default_since()

    async def check() -> list[Verdict]:
        async with source_client() as http:
            return await converse(http, args.url, secret, since=since, limit=args.limit)

    verdicts = asyncio.run(check())
    for verdict in verdicts:
        print(line(verdict))
    failed = sum(not verdict.ok for verdict in verdicts)
    if failed:
        print(f"{failed} of {len(RULES)} rules failed", file=sys.stderr)
        return 1
    print(f"every rule holds ({len(RULES)} of {len(RULES)})", file=sys.stderr)
    return 0


__all__ = ["COMMAND", "RULES", "TITLES", "Verdict", "add_command", "converse", "line", "run"]
