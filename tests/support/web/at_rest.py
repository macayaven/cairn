"""A started app brought to rest before a test counts what the store runs.

The lifespan starts the Slack keeper with the client (cairn.web.app's lifespan), and its
first pass runs on a worker thread at once; on a busy machine its statements land inside a
count taken right after. at_rest(app) stops the keeper through Keeper.stop, which sets the
keeper stopped and then takes the locks its pass and its check of bot tokens hold: it returns
only once a pass in flight has ended, and every later pass or check returns before reading.
It works before the client is entered (the first pass then reads nothing) and after.

Only the keeper runs in every app. Each other loop the lifespan starts today is refused by
name: the source sync, the sweeper, the arranger, the filer, the Google revoker and the stats
log each read the store at start, and the compactor on its first poll. The Slack keeper's
check of bot tokens needs nothing more: Keeper.stop ends it too.

What the lifespan starts is read from what it closes over, the same names it decides on,
since the app's state holds five of the seven and not the source sync or the Google revoker.
A name the lifespan no longer has is passed over here. tests/unit/web/test_at_rest_unit.py
pins the seven names and the number of tasks the lifespan starts, so a renamed variable or
a loop added later fails that one test, and not every test that calls at_rest. Without that
test a new loop would be counted and not named."""

from typing import Any

# The lifespan's name for each loop, and the name a refusal gives it.
OTHER_LOOPS = {
    "sync_every": "source sync",
    "sweeper": "sweeper",
    "arranger": "arranger",
    "filer": "filer",
    "google_connect": "Google revoker",
    "stats_log": "stats log",
    "compactor": "compactor",
}


def lifespan_of(app: Any) -> Any:
    """The function `app`'s lifespan runs, from under its context manager."""
    lifespan = app.router.lifespan_context
    return getattr(lifespan, "__wrapped__", lifespan)


def _loops_on(app: Any) -> list[str]:
    lifespan = lifespan_of(app)
    cells = dict(zip(lifespan.__code__.co_freevars, lifespan.__closure__ or (), strict=True))
    held = {name: cells[name].cell_contents for name in OTHER_LOOPS if name in cells}
    on = [name for name, loop in held.items() if loop is not None]
    if "arranger" in on and not held["arranger"].every:  # an arranger without a loop
        on.remove("arranger")
    return [OTHER_LOOPS[name] for name in on]


def at_rest(app: Any) -> None:
    """Stop `app`'s keeper; return once no pass of it runs and none will read again. An app
    with another loop on is refused, naming it (AssertionError)."""
    on = _loops_on(app)
    assert not on, f"at_rest stops the keeper only; this app also runs: {', '.join(on)}"
    app.state.slack_keeper.stop()
