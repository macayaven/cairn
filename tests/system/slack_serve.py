"""`python -m cairn.server` for the Slack journey in test_journey.py, with two things changed and
nothing else: where Slack is, and the store's clock.

`serve` has no option for where Slack is, on purpose (cairn.web.slack.Slack). This launcher
wraps the one function `serve` reads the switch through, serve_switch, so that the switch it
returns, read from the same options and the same CAIRN_SLACK_KEY, calls the stand-in at
JOURNEY_SLACK_API and answers through reply addresses on 127.0.0.1 (the stand-in's), not
Slack's own. JOURNEY_DAYS_AHEAD, when set, moves the store's clock (cairn.store.base._clock,
read at each call) that many days ahead, so a restart can stand for days passing; the clock
requests are timed by, and so the window of Slack's signatures, stays the real one. Everything
else is the real command: its options, its checks, create_app, uvicorn and the process."""

import dataclasses
import os
import sys
from datetime import timedelta

import cairn.store.base
from cairn.server import main
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
if ahead := os.environ.get("JOURNEY_DAYS_AHEAD"):
    real_clock = cairn.store.base._clock
    cairn.store.base._clock = lambda: real_clock() + timedelta(days=float(ahead))

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
