"""`python -m cairn.server` for the Slack journey in test_journey.py, with three things changed
and nothing else: where Slack is, the store's clock, and two waits made long.

`serve` has no option for where Slack is, on purpose (cairn.web.slack.Slack). This launcher
wraps the one function `serve` reads the switch through, serve_switch, so that the switch it
returns, read from the same options and the same CAIRN_SLACK_KEY, calls the stand-in at
JOURNEY_SLACK_API and answers through reply addresses on 127.0.0.1 (the stand-in's), not
Slack's own. JOURNEY_SERVER_TIME fixes the server's wall clock for the store and for Slack
request timing. The journey can set that time apart from the stand-in's clock and advance it
on restart.

The server gives Slack 1.5 seconds to say who wrote a message and a note 1 second to get its
turn (cairn.slack.people). Those limits are pinned by the acceptance tests, with no real time
passing. A journey on a loaded runner could miss either by chance, and it is not about them:
both are 30 seconds here. Everything else is the real command: its options, its checks,
create_app, uvicorn and the process."""

import dataclasses
import os
import sys
from datetime import datetime

import cairn.store.base
import cairn.web.app
from cairn.server import main
from cairn.slack import people
from cairn.web import slack as web_slack

serve_switch = web_slack.serve_switch


def to_the_stand_in(args, environ):
    switch = serve_switch(args, environ)
    if switch is None:
        return None
    return dataclasses.replace(
        switch, api=environ["JOURNEY_SLACK_API"], reply_hosts=frozenset({"127.0.0.1"})
    )


web_slack.serve_switch = to_the_stand_in
people.AUTHOR_WITHIN = people.TURN_WITHIN = 30.0  # read at each call: see the docstring
if stamp := os.environ.get("JOURNEY_SERVER_TIME"):
    fixed_time = datetime.fromisoformat(stamp)

    def journey_clock():
        return fixed_time

    cairn.store.base._clock = cairn.web.app._utc_now = journey_clock

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
