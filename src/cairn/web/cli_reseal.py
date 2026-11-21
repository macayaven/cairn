"""`python -m cairn.server reseal --db PATH [--google-connect] [--slack]` (cairn.web.cli
dispatches here): every value a sealing key sealed in the database, sealed again under the
current key, so that the earlier keys can be removed (cairn.store.reseal says what is read and
what is written).

`--google-connect` takes the key from CAIRN_GOOGLE_CONNECT_KEY and the earlier keys from
CAIRN_GOOGLE_CONNECT_KEY_PREVIOUS (it needs no OAuth client); `--slack` from CAIRN_SLACK_KEY and
CAIRN_SLACK_KEY_PREVIOUS; at least one of the two. Read as `serve` reads them: a variable
missing or malformed, or an earlier key equal to the current one, exits 2 with one line that
names the variable and never a value, and nothing is written. No database at the path exits 1;
one this cairn cannot open is refused as every command refuses it (cairn.web.cli.main).

It prints one line per key, in counts only: how many values it sealed again, how many no key
given opens (left as they were: removing the earlier keys loses nothing more of them), and how
many the current key sealed already. Exit 0. Run twice, the second run seals nothing again and
writes nothing. A server may run beside it.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from cairn.store import Store
from cairn.store.reseal import Resealed, reseal_google, reseal_slack
from cairn.web import google_connect
from cairn.web import slack as web_slack
from cairn.web.cli_data import DB_EXISTS

COMMAND = "reseal"
NO_KEY_NAMED = "reseal needs --google-connect, --slack or both: the keys whose values it seals"
REFUSED = "cannot reseal: {}"


def add_command(commands: Any) -> None:
    reseal = commands.add_parser(
        COMMAND,
        help="seal every stored Google token or Slack secret again under the current key, so"
        " the earlier keys (<VARIABLE>_PREVIOUS) can be removed",
        allow_abbrev=False,
    )
    reseal.add_argument("--db", type=Path, required=True, help=DB_EXISTS)
    reseal.add_argument(
        "--google-connect",
        action="store_true",
        help=f"the connected Google accounts' tokens, under {google_connect.KEY_VARIABLE}",
    )
    reseal.add_argument(
        "--slack",
        action="store_true",
        help=f"the Slack installs' signing secrets and bot tokens, under {web_slack.KEY_VARIABLE}",
    )


def said(variable: str, done: Resealed) -> str:
    values = "value" if done.resealed == 1 else "values"
    return (
        f"{variable}: {done.resealed} {values} sealed again under the current key,"
        f" {done.unopened} that no key given opens left as it was, {done.current} already"
        " under it"
    )


def run(args: argparse.Namespace) -> int:
    if not (args.google_connect or args.slack):
        print(NO_KEY_NAMED, file=sys.stderr)
        return 2
    db: Path = args.db
    if not db.is_file():
        print(f"no database at {db}", file=sys.stderr)
        return 1
    store = Store.open_existing(db)  # refuses a database it cannot open before anything is read
    try:
        try:  # names a variable, never a value
            google = google_connect.read_sealer(os.environ) if args.google_connect else None
            slack = web_slack.read_switch(os.environ).sealer() if args.slack else None
        except ValueError as error:
            print(REFUSED.format(error), file=sys.stderr)
            return 2
        if google is not None:
            print(said(google_connect.KEY_VARIABLE, reseal_google(store, google)))
        if slack is not None:
            print(said(web_slack.KEY_VARIABLE, reseal_slack(store, slack)))
    finally:
        store.close()
    return 0
